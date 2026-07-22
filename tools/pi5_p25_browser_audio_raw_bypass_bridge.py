#!/usr/bin/env python3
"""Raw OP25 UDP PCM to browser WAV bridge for PI-P25 V0.3M bypass testing.

This bridge intentionally does not gate, mute, drop, or smooth audio. It counts
OP25 2-byte UDP control flags but ignores them for playback. The purpose is an
A/B baseline: prove whether OP25 is still delivering clear audio to a browser
when all project-side audio filters are bypassed.
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

PCM_RATE_HZ = 8000
PCM_CHANNELS = 1
PCM_BITS = 16
OP25_AUDIO_FRAME_BYTES = 320
DEFAULT_HTTP_HOST = "0.0.0.0"
DEFAULT_HTTP_PORT = 8072
DEFAULT_UDP_HOST = "127.0.0.1"
DEFAULT_UDP_PORT = 23456
DEFAULT_MAX_QUEUE_CHUNKS = 9000
SILENCE_FRAME = b"\x00" * OP25_AUDIO_FRAME_BYTES


def wav_header() -> bytes:
    byte_rate = PCM_RATE_HZ * PCM_CHANNELS * PCM_BITS // 8
    block_align = PCM_CHANNELS * PCM_BITS // 8
    data_size = 0x7FFF0000
    riff_size = 36 + data_size
    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", riff_size),
            b"WAVE",
            b"fmt ",
            struct.pack("<IHHIIHH", 16, 1, PCM_CHANNELS, PCM_RATE_HZ, byte_rate, block_align, PCM_BITS),
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
    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + data_size),
            b"WAVE",
            b"fmt ",
            struct.pack("<IHHIIHH", 16, 1, PCM_CHANNELS, PCM_RATE_HZ, byte_rate, block_align, PCM_BITS),
            b"data",
            struct.pack("<I", data_size),
            bytes(frames),
        ]
    )


@dataclass
class AudioState:
    max_queue_chunks: int = DEFAULT_MAX_QUEUE_CHUNKS
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
    queue_overflow_drops: int = 0
    started_utc: float = field(default_factory=time.time)
    last_packet_utc: float | None = None
    last_audio_utc: float | None = None
    last_sent_utc: float | None = None
    last_flag_utc: float | None = None
    last_flag_value: int | None = None
    bind_errors: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.chunks = deque(maxlen=self.max_queue_chunks)

    def add_packet(self, payload: bytes) -> None:
        now = time.time()
        with self.lock:
            self.packets += 1
            self.bytes_received += len(payload)
            self.last_packet_utc = now
            if len(payload) == OP25_AUDIO_FRAME_BYTES:
                self.audio_packets += 1
                self.last_audio_utc = now
                if len(self.chunks) >= self.max_queue_chunks:
                    self.queue_overflow_drops += 1
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
                return
            self.ignored_packets += 1

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
                "mode": "raw-bypass-v0.3m",
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
                "queue_overflow_drops": self.queue_overflow_drops,
                "chunks_sent": self.chunks_sent,
                "silence_chunks_sent": self.silence_chunks_sent,
                "underruns": self.underruns,
                "stream_clients": self.stream_clients,
                "gates_enabled": False,
                "audio_dropped_by_flag": 0,
                "audio_dropped_by_log_gate": 0,
                "log_gate_events": 0,
                "last_packet_age_seconds": None if self.last_packet_utc is None else round(now - self.last_packet_utc, 3),
                "last_audio_age_seconds": None if self.last_audio_utc is None else round(now - self.last_audio_utc, 3),
                "last_sent_age_seconds": None if self.last_sent_utc is None else round(now - self.last_sent_utc, 3),
                "last_flag_age_seconds": None if self.last_flag_utc is None else round(now - self.last_flag_utc, 3),
                "last_flag_value": self.last_flag_value,
                "uptime_seconds": round(now - self.started_utc, 3),
                "bind_errors": list(self.bind_errors),
                "stream_path": "/audio.wav",
                "test_tone_path": "/test-tone.wav",
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
    server_version = "PiP25RawBypassBridge/0.3M"

    @property
    def audio_state(self) -> AudioState:
        return self.server.audio_state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/" or self.path.startswith("/api/audio/status"):
            self._send_json(self.audio_state.snapshot())
            return
        if self.path.startswith("/test-tone.wav"):
            body = generated_tone_wav()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/audio.wav"):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Connection", "close")
            self._cors()
            self.end_headers()
            self.wfile.write(wav_header())
            self.wfile.flush()
            self.audio_state.client_started()
            frame_seconds = OP25_AUDIO_FRAME_BYTES / (PCM_RATE_HZ * PCM_CHANNELS * (PCM_BITS // 8))
            try:
                while True:
                    frame = self.audio_state.pop_audio()
                    if frame is None:
                        frame = SILENCE_FRAME
                        self.audio_state.note_silence_sent()
                    self.wfile.write(frame)
                    self.wfile.flush()
                    time.sleep(frame_seconds)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            finally:
                self.audio_state.client_ended()
            return
        self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)


def self_test() -> int:
    header = wav_header()
    tone = generated_tone_wav(0.1)
    state = AudioState()
    state.add_packet(b"\x00" * OP25_AUDIO_FRAME_BYTES)
    state.add_packet(b"\x00\x00")
    snap = state.snapshot()
    assert header.startswith(b"RIFF") and b"WAVE" in header[:16]
    assert tone.startswith(b"RIFF") and len(tone) > 44
    assert snap["audio_packets"] == 1
    assert snap["flag_packets"] == 1
    assert snap["gates_enabled"] is False
    print("PASS: raw bypass bridge self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PI-P25 raw bypass browser audio bridge")
    parser.add_argument("--host", default=DEFAULT_HTTP_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    parser.add_argument("--udp-host", default=DEFAULT_UDP_HOST)
    parser.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT)
    parser.add_argument("--max-queue-chunks", type=int, default=DEFAULT_MAX_QUEUE_CHUNKS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    state = AudioState(max_queue_chunks=args.max_queue_chunks)
    receivers = [
        UdpReceiver(state, args.udp_host, args.udp_port),
        UdpReceiver(state, args.udp_host, args.udp_port + 1),
    ]
    for receiver in receivers:
        receiver.start()

    server = ThreadingHTTPServer((args.host, args.port), AudioHandler)
    server.audio_state = state  # type: ignore[attr-defined]

    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    print(
        json.dumps(
            {
                "ok": True,
                "mode": "raw-bypass-v0.3m",
                "http": f"http://{args.host}:{args.port}/audio.wav",
                "udp_ports": [args.udp_port, args.udp_port + 1],
                "gates_enabled": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        for receiver in receivers:
            receiver.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
