#!/usr/bin/env python3
"""PI-P25 browser audio bridge server.

Receives OP25 UDP PCM frames on localhost and exposes a browser-readable WAV
stream.  The Raspberry Pi remains the RF/decoder host; playback happens in the
browser host.
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
from urllib.parse import urlparse

PCM_RATE_HZ = 8000
PCM_CHANNELS = 1
PCM_BITS = 16
DEFAULT_UDP_HOST = "127.0.0.1"
DEFAULT_UDP_PORT = 23456
DEFAULT_HTTP_HOST = "0.0.0.0"
DEFAULT_HTTP_PORT = 8072
MAX_QUEUE_CHUNKS = 9000
OP25_AUDIO_FRAME_BYTES = 320
SILENCE_FRAME = b"\x00" * OP25_AUDIO_FRAME_BYTES


def wav_header(sample_rate: int = PCM_RATE_HZ, channels: int = PCM_CHANNELS, bits: int = PCM_BITS) -> bytes:
    """Return a long-form PCM WAV header suitable for streaming."""

    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    # Large placeholder sizes allow browsers to begin playback before EOF.
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
    chunks: deque[bytes] = field(default_factory=lambda: deque(maxlen=MAX_QUEUE_CHUNKS))
    packets: int = 0
    audio_packets: int = 0
    flag_packets: int = 0
    ignored_packets: int = 0
    bytes_received: int = 0
    started_utc: float = field(default_factory=time.time)
    last_packet_utc: float | None = None
    last_audio_utc: float | None = None
    bind_errors: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

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
            elif len(payload) == 2:
                self.flag_packets += 1
            else:
                self.ignored_packets += 1

    def pop_audio(self) -> bytes | None:
        with self.lock:
            if not self.chunks:
                return None
            return self.chunks.popleft()

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            queued = len(self.chunks)
            return {
                "ok": True,
                "sample_rate_hz": PCM_RATE_HZ,
                "channels": PCM_CHANNELS,
                "bits_per_sample": PCM_BITS,
                "format": "s16le-mono-pcm-wav-stream",
                "packets": self.packets,
                "audio_packets": self.audio_packets,
                "flag_packets": self.flag_packets,
                "ignored_packets": self.ignored_packets,
                "bytes_received": self.bytes_received,
                "queued_chunks": queued,
                "last_packet_age_seconds": None if self.last_packet_utc is None else round(now - self.last_packet_utc, 3),
                "last_audio_age_seconds": None if self.last_audio_utc is None else round(now - self.last_audio_utc, 3),
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
    server_version = "PiP25BrowserAudioBridge/0.3D"

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

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/api/audio/status"):
            self._send_json(self.audio_state.snapshot())
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
            try:
                self.wfile.write(wav_header())
                self.wfile.flush()
                while True:
                    chunk = self.audio_state.pop_audio() or SILENCE_FRAME
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    time.sleep(len(chunk) / 2 / PCM_RATE_HZ)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
        self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)


class AudioServer(ThreadingHTTPServer):
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
    state = AudioState()
    state.add_packet(b"\x01\x00")
    state.add_packet(b"\x00" * OP25_AUDIO_FRAME_BYTES)
    snap = state.snapshot()
    if snap["flag_packets"] != 1 or snap["audio_packets"] != 1:
        print("FAIL: UDP packet accounting invalid")
        return 1
    print("PASS: browser audio bridge self-test")
    print("FINAL: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the PI-P25 browser audio bridge server")
    parser.add_argument("--host", default=DEFAULT_HTTP_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    parser.add_argument("--udp-host", default=DEFAULT_UDP_HOST)
    parser.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    state = AudioState()
    receivers = [UdpReceiver(state, args.udp_host, args.udp_port), UdpReceiver(state, args.udp_host, args.udp_port + 1)]
    for receiver in receivers:
        receiver.start()

    httpd = AudioServer((args.host, args.port), AudioHandler, state)

    def stop(_signum: int, _frame: Any) -> None:
        for receiver in receivers:
            receiver.stop()
        httpd.shutdown()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"PI P25 browser audio bridge listening on http://{args.host}:{args.port}", flush=True)
    print(f"Receiving OP25 UDP PCM on {args.udp_host}:{args.udp_port} and {args.udp_port + 1}", flush=True)
    try:
        httpd.serve_forever()
    finally:
        for receiver in receivers:
            receiver.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
