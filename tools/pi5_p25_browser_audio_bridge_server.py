#!/usr/bin/env python3
"""PI-P25 browser audio bridge server.

Receives OP25 UDP PCM frames on localhost and exposes a browser-readable WAV
stream. The Raspberry Pi remains the RF/decoder host; playback happens in the
browser host.

V0.3K keeps the simple raw PCM stream, honors OP25 2-byte audio control flags,
and adds an HTTP log-driven gate so encrypted-call indicators from the OP25 log
can immediately clear and suppress audio before garbled encrypted vocoder bursts
reach the browser.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import socket
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

PCM_RATE_HZ = 8000
PCM_CHANNELS = 1
PCM_BITS = 16
BYTES_PER_SAMPLE = PCM_BITS // 8
DEFAULT_UDP_HOST = "127.0.0.1"
DEFAULT_UDP_PORT = 23456
DEFAULT_HTTP_HOST = "0.0.0.0"
DEFAULT_HTTP_PORT = 8072
DEFAULT_MAX_QUEUE_CHUNKS = 9000
DEFAULT_FLAG_DROP_HOLD_MS = 2500
DEFAULT_LOG_GATE_HOLD_MS = 5000
OP25_AUDIO_FRAME_BYTES = 320
SILENCE_FRAME = b"\x00" * OP25_AUDIO_FRAME_BYTES


def wav_header(sample_rate: int = PCM_RATE_HZ, channels: int = PCM_CHANNELS, bits: int = PCM_BITS) -> bytes:
    """Return a long-form PCM WAV header suitable for streaming."""

    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = 0x7FFF0000
    riff_size = 36 + data_size
    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", riff_size),
            b"WAVE",
            b"fmt ",
            struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits),
            b"data",
            struct.pack("<I", data_size),
        ]
    )


def generated_tone_wav(seconds: float = 1.0, frequency_hz: float = 880.0) -> bytes:
    sample_count = int(PCM_RATE_HZ * seconds)
    frames = bytearray()
    for n in range(sample_count):
        sample = int(math.sin((2.0 * math.pi * frequency_hz * n) / PCM_RATE_HZ) * 12000)
        frames.extend(struct.pack("<h", sample))
    data_size = len(frames)
    byte_rate = PCM_RATE_HZ * PCM_CHANNELS * PCM_BITS // 8
    block_align = PCM_CHANNELS * PCM_BITS // 8
    header = b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + data_size),
            b"WAVE",
            b"fmt ",
            struct.pack("<IHHIIHH", 16, 1, PCM_CHANNELS, PCM_RATE_HZ, byte_rate, block_align, PCM_BITS),
            b"data",
            struct.pack("<I", data_size),
        ]
    )
    return header + bytes(frames)


@dataclass
class AudioState:
    max_queue_chunks: int = DEFAULT_MAX_QUEUE_CHUNKS
    flag_drop_hold_ms: int = DEFAULT_FLAG_DROP_HOLD_MS
    default_log_gate_hold_ms: int = DEFAULT_LOG_GATE_HOLD_MS
    chunks: deque[bytes] = field(init=False)
    packets: int = 0
    audio_packets: int = 0
    flag_packets: int = 0
    flag_zero_count: int = 0
    flag_one_count: int = 0
    flag_other_count: int = 0
    ignored_packets: int = 0
    bytes_received: int = 0
    chunks_sent: int = 0
    silence_chunks_sent: int = 0
    stream_clients: int = 0
    underruns: int = 0
    queued_chunks_dropped_by_flag: int = 0
    audio_dropped_by_flag: int = 0
    queued_chunks_dropped_by_log_gate: int = 0
    audio_dropped_by_log_gate: int = 0
    log_gate_events: int = 0
    log_gate_reasons: dict[str, int] = field(default_factory=dict)
    started_utc: float = field(default_factory=time.time)
    last_packet_utc: float | None = None
    last_audio_utc: float | None = None
    last_sent_utc: float | None = None
    last_flag_utc: float | None = None
    last_flag_value: int | None = None
    last_dropped_audio_utc: float | None = None
    last_log_gate_utc: float | None = None
    last_log_gate_reason: str | None = None
    flag_gate_until_utc: float = 0.0
    log_gate_until_utc: float = 0.0
    bind_errors: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.chunks = deque(maxlen=self.max_queue_chunks)

    def _drop_queued_locked(self, *, by_log_gate: bool) -> int:
        dropped = len(self.chunks)
        self.chunks.clear()
        if by_log_gate:
            self.queued_chunks_dropped_by_log_gate += dropped
        else:
            self.queued_chunks_dropped_by_flag += dropped
        return dropped

    def add_packet(self, payload: bytes) -> None:
        now = time.time()
        with self.lock:
            self.packets += 1
            self.bytes_received += len(payload)
            self.last_packet_utc = now
            if len(payload) == OP25_AUDIO_FRAME_BYTES:
                self.audio_packets += 1
                self.last_audio_utc = now
                if now < self.log_gate_until_utc:
                    self.audio_dropped_by_log_gate += 1
                    self.last_dropped_audio_utc = now
                    return
                if now < self.flag_gate_until_utc:
                    self.audio_dropped_by_flag += 1
                    self.last_dropped_audio_utc = now
                    return
                self.chunks.append(payload)
                return
            if len(payload) == 2:
                value = int.from_bytes(payload, byteorder="little", signed=False)
                self.flag_packets += 1
                self.last_flag_utc = now
                self.last_flag_value = value
                if value == 0:
                    self.flag_zero_count += 1
                elif value == 1:
                    self.flag_one_count += 1
                else:
                    self.flag_other_count += 1
                self.flag_gate_until_utc = max(self.flag_gate_until_utc, now + (self.flag_drop_hold_ms / 1000.0))
                self._drop_queued_locked(by_log_gate=False)
                return
            self.ignored_packets += 1

    def apply_log_gate(self, hold_ms: int, reason: str) -> dict[str, Any]:
        now = time.time()
        safe_reason = (reason or "op25-log").strip()[:96] or "op25-log"
        with self.lock:
            self.log_gate_events += 1
            self.log_gate_reasons[safe_reason] = self.log_gate_reasons.get(safe_reason, 0) + 1
            self.last_log_gate_utc = now
            self.last_log_gate_reason = safe_reason
            self.log_gate_until_utc = max(self.log_gate_until_utc, now + (max(0, hold_ms) / 1000.0))
            dropped = self._drop_queued_locked(by_log_gate=True)
            return {
                "ok": True,
                "mode": "encrypted-log-gate-v0.3k",
                "reason": safe_reason,
                "hold_ms": hold_ms,
                "queued_chunks_dropped": dropped,
                "log_gate_events": self.log_gate_events,
                "log_gate_remaining_seconds": round(max(0.0, self.log_gate_until_utc - now), 3),
            }

    def queue_depth(self) -> int:
        with self.lock:
            return len(self.chunks)

    def pop_audio(self) -> bytes | None:
        with self.lock:
            if not self.chunks:
                self.underruns += 1
                return None
            self.last_sent_utc = time.time()
            self.chunks_sent += 1
            return self.chunks.popleft()

    def note_silence_sent(self) -> None:
        with self.lock:
            self.last_sent_utc = time.time()
            self.silence_chunks_sent += 1

    def client_started(self) -> None:
        with self.lock:
            self.stream_clients += 1

    def client_ended(self) -> None:
        with self.lock:
            self.stream_clients = max(0, self.stream_clients - 1)

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            return {
                "ok": True,
                "mode": "encrypted-log-gate-v0.3k",
                "sample_rate_hz": PCM_RATE_HZ,
                "channels": PCM_CHANNELS,
                "bits_per_sample": PCM_BITS,
                "format": "s16le-mono-pcm-wav-stream",
                "packets": self.packets,
                "audio_packets": self.audio_packets,
                "flag_packets": self.flag_packets,
                "flag_zero_count": self.flag_zero_count,
                "flag_one_count": self.flag_one_count,
                "flag_other_count": self.flag_other_count,
                "ignored_packets": self.ignored_packets,
                "bytes_received": self.bytes_received,
                "queued_chunks": len(self.chunks),
                "max_queue_chunks": self.max_queue_chunks,
                "chunks_sent": self.chunks_sent,
                "silence_chunks_sent": self.silence_chunks_sent,
                "underruns": self.underruns,
                "stream_clients": self.stream_clients,
                "flag_drop_hold_ms": self.flag_drop_hold_ms,
                "flag_drop_active": now < self.flag_gate_until_utc,
                "flag_drop_remaining_seconds": round(max(0.0, self.flag_gate_until_utc - now), 3),
                "queued_chunks_dropped_by_flag": self.queued_chunks_dropped_by_flag,
                "audio_dropped_by_flag": self.audio_dropped_by_flag,
                "log_gate_hold_ms_default": self.default_log_gate_hold_ms,
                "log_gate_active": now < self.log_gate_until_utc,
                "log_gate_remaining_seconds": round(max(0.0, self.log_gate_until_utc - now), 3),
                "log_gate_events": self.log_gate_events,
                "log_gate_reasons": dict(self.log_gate_reasons),
                "last_log_gate_reason": self.last_log_gate_reason,
                "queued_chunks_dropped_by_log_gate": self.queued_chunks_dropped_by_log_gate,
                "audio_dropped_by_log_gate": self.audio_dropped_by_log_gate,
                "last_packet_age_seconds": None if self.last_packet_utc is None else round(now - self.last_packet_utc, 3),
                "last_audio_age_seconds": None if self.last_audio_utc is None else round(now - self.last_audio_utc, 3),
                "last_sent_age_seconds": None if self.last_sent_utc is None else round(now - self.last_sent_utc, 3),
                "last_flag_age_seconds": None if self.last_flag_utc is None else round(now - self.last_flag_utc, 3),
                "last_flag_value": self.last_flag_value,
                "last_dropped_audio_age_seconds": None if self.last_dropped_audio_utc is None else round(now - self.last_dropped_audio_utc, 3),
                "last_log_gate_age_seconds": None if self.last_log_gate_utc is None else round(now - self.last_log_gate_utc, 3),
                "uptime_seconds": round(now - self.started_utc, 3),
                "bind_errors": list(self.bind_errors),
                "stream_path": "/audio.wav",
                "test_tone_path": "/test-tone.wav",
                "gate_path": "/api/audio/gate",
            }


class UdpReceiver(threading.Thread):
    def __init__(self, state: AudioState, host: str, port: int) -> None:
        super().__init__(daemon=True)
        self.state = state
        self.host = host
        self.port = port
        self.keep_running = True
        self.sock: socket.socket | None = None

    def run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.5)
            sock.bind((self.host, self.port))
            self.sock = sock
        except OSError as exc:
            with self.state.lock:
                self.state.bind_errors.append(f"{self.host}:{self.port}: {exc}")
            return
        while self.keep_running:
            try:
                payload, _addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            self.state.add_packet(payload)

    def stop(self) -> None:
        self.keep_running = False
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass


class AudioHandler(BaseHTTPRequestHandler):
    server_version = "PiP25BrowserAudioBridge/0.3K"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    @property
    def audio_state(self) -> AudioState:
        return self.server.audio_state  # type: ignore[attr-defined]

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream_audio(self) -> None:
        self.audio_state.client_started()
        try:
            self.wfile.write(wav_header())
            self.wfile.flush()
            while True:
                chunk = self.audio_state.pop_audio()
                if chunk is None:
                    chunk = SILENCE_FRAME
                    self.audio_state.note_silence_sent()
                self.wfile.write(chunk)
                self.wfile.flush()
                time.sleep(len(chunk) / BYTES_PER_SAMPLE / PCM_RATE_HZ)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            self.audio_state.client_ended()

    def _gate_from_query(self, query: str) -> dict[str, Any]:
        params = parse_qs(query)
        hold_raw = (params.get("hold_ms") or params.get("ms") or [str(self.audio_state.default_log_gate_hold_ms)])[0]
        reason = (params.get("reason") or ["op25-log"])[0]
        try:
            hold_ms = int(hold_raw)
        except ValueError:
            hold_ms = self.audio_state.default_log_gate_hold_ms
        hold_ms = max(0, min(30000, hold_ms))
        return self.audio_state.apply_log_gate(hold_ms=hold_ms, reason=reason)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/audio/gate":
            self._send_json(self._gate_from_query(parsed.query))
            return
        self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/api/audio/status"):
            self._send_json(self.audio_state.snapshot())
            return
        if path == "/api/audio/gate":
            self._send_json(self._gate_from_query(parsed.query))
            return
        if path == "/test-tone.wav":
            data = generated_tone_wav()
            self.send_response(HTTPStatus.OK)
            self._cors()
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/audio.wav":
            self.send_response(HTTPStatus.OK)
            self._cors()
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Connection", "close")
            self.end_headers()
            self._stream_audio()
            return
        self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)


class AudioServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], audio_state: AudioState) -> None:
        super().__init__(server_address, handler_class)
        self.audio_state = audio_state


def self_test() -> int:
    header = wav_header()
    if not header.startswith(b"RIFF") or b"WAVE" not in header:
        print("FAIL: streaming WAV header invalid")
        return 1
    tone = generated_tone_wav(0.1)
    if not tone.startswith(b"RIFF") or len(tone) < 100:
        print("FAIL: generated tone WAV invalid")
        return 1
    state = AudioState(flag_drop_hold_ms=1000, default_log_gate_hold_ms=2000)
    state.add_packet(b"\x00" * OP25_AUDIO_FRAME_BYTES)
    state.add_packet((0).to_bytes(2, "little"))
    state.add_packet(b"\x01" * OP25_AUDIO_FRAME_BYTES)
    snap = state.snapshot()
    if snap["flag_zero_count"] != 1 or snap["audio_dropped_by_flag"] != 1:
        print("FAIL: flag gate accounting invalid")
        return 1
    state.apply_log_gate(hold_ms=2000, reason="self-test")
    state.add_packet(b"\x02" * OP25_AUDIO_FRAME_BYTES)
    snap = state.snapshot()
    if snap["log_gate_events"] != 1 or snap["audio_dropped_by_log_gate"] != 1:
        print("FAIL: log gate accounting invalid")
        return 1
    print("PASS: encrypted-log-gate browser audio bridge self-test")
    print("FINAL: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the PI-P25 browser audio bridge server")
    parser.add_argument("--host", default=DEFAULT_HTTP_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    parser.add_argument("--udp-host", default=DEFAULT_UDP_HOST)
    parser.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT)
    parser.add_argument("--max-queue-chunks", type=int, default=DEFAULT_MAX_QUEUE_CHUNKS)
    parser.add_argument("--flag-drop-hold-ms", type=int, default=DEFAULT_FLAG_DROP_HOLD_MS)
    parser.add_argument("--encrypted-log-hold-ms", type=int, default=DEFAULT_LOG_GATE_HOLD_MS)
    parser.add_argument("--prebuffer-chunks", type=int, default=0, help="accepted for compatibility; ignored")
    parser.add_argument("--declick-samples", type=int, default=0, help="accepted for compatibility; ignored")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.max_queue_chunks < 10:
        parser.error("--max-queue-chunks must be at least 10")
    if args.flag_drop_hold_ms < 0 or args.flag_drop_hold_ms > 30000:
        parser.error("--flag-drop-hold-ms must be between 0 and 30000")
    if args.encrypted_log_hold_ms < 0 or args.encrypted_log_hold_ms > 30000:
        parser.error("--encrypted-log-hold-ms must be between 0 and 30000")

    state = AudioState(
        max_queue_chunks=args.max_queue_chunks,
        flag_drop_hold_ms=args.flag_drop_hold_ms,
        default_log_gate_hold_ms=args.encrypted_log_hold_ms,
    )
    receivers = [UdpReceiver(state, args.udp_host, args.udp_port), UdpReceiver(state, args.udp_host, args.udp_port + 1)]
    for receiver in receivers:
        receiver.start()

    httpd = AudioServer((args.host, args.port), AudioHandler, state)

    def stop(_signum: int, _frame: Any) -> None:
        for receiver in receivers:
            receiver.stop()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"PI P25 browser audio bridge listening on http://{args.host}:{args.port}", flush=True)
    print(f"Receiving OP25 UDP PCM on {args.udp_host}:{args.udp_port} and {args.udp_port + 1}", flush=True)
    print(
        f"Mode encrypted-log-gate-v0.3k flag_drop_hold_ms={args.flag_drop_hold_ms} encrypted_log_hold_ms={args.encrypted_log_hold_ms}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    finally:
        for receiver in receivers:
            receiver.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
