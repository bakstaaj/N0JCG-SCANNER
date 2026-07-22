#!/usr/bin/env python3
"""Raw PI-P25 browser audio bridge.

Receives OP25 UDP PCM frames on localhost and exposes a browser-readable WAV
stream. This is the normal clear-audio baseline path: it counts OP25 2-byte
control flag packets but does not use them to drop, gate, or modify audio.
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
SILENCE_FRAME = b"\x00" * OP25_AUDIO_FRAME_BYTES
DEFAULT_HTTP_HOST = "0.0.0.0"
DEFAULT_HTTP_PORT = 8072
DEFAULT_UDP_HOST = "127.0.0.1"
DEFAULT_UDP_PORT = 23456
DEFAULT_MAX_QUEUE_CHUNKS = 9000


def wav_header(sample_rate: int = PCM_RATE_HZ, channels: int = PCM_CHANNELS, bits: int = PCM_BITS) -> bytes:
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
                "mode": "raw-clear-v0.3o",
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


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class AudioHandler(BaseHTTPRequestHandler):
    server_version = "PiP25RawBrowserAudioBridge/0.3O"

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
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/audio/status":
            self._send_json(self.audio_state.snapshot())
            return
        if self.path == "/test-tone.wav":
            data = generated_tone_wav()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path != "/audio.wav":
            self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "audio/wav")
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(wav_header())
            self.wfile.flush()
            self.audio_state.client_started()
            while True:
                payload = self.audio_state.pop_audio()
                if payload is None:
                    payload = SILENCE_FRAME
                    self.audio_state.note_silence_sent()
                self.wfile.write(payload)
                self.wfile.flush()
                time.sleep(0.02)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            self.audio_state.client_ended()


def self_test() -> int:
    state = AudioState()
    state.add_packet(SILENCE_FRAME)
    state.add_packet((0).to_bytes(2, "little"))
    snap = state.snapshot()
    if snap["audio_packets"] != 1 or snap["flag_packets"] != 1:
        print("FAIL: raw bridge self-test packet counters incorrect")
        return 1
    print("PASS: raw browser audio bridge self-test")
    print("FINAL: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Raw OP25 UDP PCM to browser WAV bridge")
    parser.add_argument("--host", default=DEFAULT_HTTP_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    parser.add_argument("--udp-host", default=DEFAULT_UDP_HOST)
    parser.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT)
    parser.add_argument("--max-queue-chunks", type=int, default=DEFAULT_MAX_QUEUE_CHUNKS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    state = AudioState(max_queue_chunks=args.max_queue_chunks)
    receivers = [UdpReceiver(state, args.udp_host, args.udp_port), UdpReceiver(state, args.udp_host, args.udp_port + 1)]
    for receiver in receivers:
        receiver.start()

    try:
        httpd = ReusableThreadingHTTPServer((args.host, args.port), AudioHandler)
    except OSError as exc:
        print(f"FAIL: HTTP bind failed on {args.host}:{args.port}: {exc}", flush=True)
        return 1
    httpd.audio_state = state  # type: ignore[attr-defined]

    def request_stop(_signum: int, _frame: Any) -> None:
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    print(
        f"PI-P25 raw browser audio bridge listening on http://{args.host}:{args.port}/audio.wav "
        f"udp={args.udp_host}:{args.udp_port},{args.udp_port + 1}",
        flush=True,
    )
    try:
        httpd.serve_forever(poll_interval=0.2)
    finally:
        for receiver in receivers:
            receiver.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
