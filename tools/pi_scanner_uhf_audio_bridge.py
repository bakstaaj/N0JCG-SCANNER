#!/usr/bin/env python3
"""Isolated UHF UDP PCM to browser WAV bridge.

This service listens only to the UHF worker on UDP 23459 and exposes a separate
browser audio stream on HTTP 8074. It does not bind or alter the P25 or VHF ports.
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
AUDIO_FRAME_BYTES = 320
SILENCE_FRAME = b"\x00" * AUDIO_FRAME_BYTES
DEFAULT_HTTP_HOST = "0.0.0.0"
DEFAULT_HTTP_PORT = 8074
DEFAULT_UDP_HOST = "127.0.0.1"
DEFAULT_UDP_PORT = 23459
DEFAULT_MAX_QUEUE_CHUNKS = 1500


def wav_header() -> bytes:
    byte_rate = PCM_RATE_HZ * PCM_CHANNELS * PCM_BITS // 8
    block_align = PCM_CHANNELS * PCM_BITS // 8
    data_size = 0x7FFF0000
    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + data_size),
            b"WAVE",
            b"fmt ",
            struct.pack(
                "<IHHIIHH",
                16,
                1,
                PCM_CHANNELS,
                PCM_RATE_HZ,
                byte_rate,
                block_align,
                PCM_BITS,
            ),
            b"data",
            struct.pack("<I", data_size),
        ]
    )


def generated_tone_wav(
    seconds: float = 1.0,
    frequency_hz: float = 880.0,
) -> bytes:
    frames = bytearray()
    for index in range(int(PCM_RATE_HZ * seconds)):
        sample = int(
            math.sin(
                (2.0 * math.pi * frequency_hz * index) / PCM_RATE_HZ
            )
            * 12000
        )
        frames.extend(struct.pack("<h", sample))
    data_size = len(frames)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVEfmt "
        + struct.pack(
            "<IHHIIHH",
            16,
            1,
            PCM_CHANNELS,
            PCM_RATE_HZ,
            PCM_RATE_HZ * PCM_CHANNELS * PCM_BITS // 8,
            PCM_CHANNELS * PCM_BITS // 8,
            PCM_BITS,
        )
        + b"data"
        + struct.pack("<I", data_size)
        + bytes(frames)
    )


@dataclass
class UhfAudioState:
    max_queue_chunks: int = DEFAULT_MAX_QUEUE_CHUNKS
    queue: deque[bytes] = field(init=False)
    packets: int = 0
    accepted_frames: int = 0
    ignored_packets: int = 0
    bytes_received: int = 0
    frames_sent: int = 0
    silence_frames_sent: int = 0
    stream_clients: int = 0
    underruns: int = 0
    started_utc: float = field(default_factory=time.time)
    last_packet_utc: float | None = None
    last_sent_utc: float | None = None
    bind_error: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.queue = deque(maxlen=max(100, self.max_queue_chunks))

    def add_packet(self, payload: bytes) -> None:
        with self.lock:
            self.packets += 1
            self.bytes_received += len(payload)
            self.last_packet_utc = time.time()
            if len(payload) != AUDIO_FRAME_BYTES:
                self.ignored_packets += 1
                return
            self.accepted_frames += 1
            self.queue.append(payload)

    def pop_audio(self) -> bytes | None:
        with self.lock:
            if not self.queue:
                self.underruns += 1
                return None
            self.frames_sent += 1
            self.last_sent_utc = time.time()
            return self.queue.popleft()

    def note_silence(self) -> None:
        with self.lock:
            self.silence_frames_sent += 1
            self.last_sent_utc = time.time()

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
                "ok": not bool(self.bind_error),
                "mode": "isolated-uhf-browser-audio",
                "http_port": DEFAULT_HTTP_PORT,
                "udp_port": DEFAULT_UDP_PORT,
                "sample_rate_hz": PCM_RATE_HZ,
                "channels": PCM_CHANNELS,
                "bits_per_sample": PCM_BITS,
                "format": "s16le-mono-pcm-wav-stream",
                "packets": self.packets,
                "accepted_frames": self.accepted_frames,
                "ignored_packets": self.ignored_packets,
                "bytes_received": self.bytes_received,
                "queued_frames": len(self.queue),
                "frames_sent": self.frames_sent,
                "silence_frames_sent": self.silence_frames_sent,
                "stream_clients": self.stream_clients,
                "underruns": self.underruns,
                "last_packet_age_seconds": (
                    None
                    if self.last_packet_utc is None
                    else round(now - self.last_packet_utc, 3)
                ),
                "last_sent_age_seconds": (
                    None
                    if self.last_sent_utc is None
                    else round(now - self.last_sent_utc, 3)
                ),
                "uptime_seconds": round(now - self.started_utc, 3),
                "bind_error": self.bind_error,
                "stream_path": "/audio.wav",
                "status_path": "/api/audio/status",
                "test_tone_path": "/test-tone.wav",
            }


class UdpReceiver(threading.Thread):
    def __init__(
        self,
        state: UhfAudioState,
        host: str,
        port: int,
    ) -> None:
        super().__init__(daemon=True)
        self.state = state
        self.host = host
        self.port = port
        self.keep_running = True
        self.socket: socket.socket | None = None

    def run(self) -> None:
        try:
            receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            receiver.settimeout(0.5)
            receiver.bind((self.host, self.port))
            self.socket = receiver
        except OSError as exc:
            with self.state.lock:
                self.state.bind_error = f"{self.host}:{self.port}: {exc}"
            return

        while self.keep_running:
            try:
                payload, _address = receiver.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            self.state.add_packet(payload)

    def stop(self) -> None:
        self.keep_running = False
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class AudioHandler(BaseHTTPRequestHandler):
    server_version = "PiScannerUhfAudio/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    @property
    def state(self) -> UhfAudioState:
        return self.server.audio_state  # type: ignore[attr-defined]

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")

    def _send_json(
        self,
        payload: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        request_path = self.path.split("?", 1)[0]
        if request_path == "/api/audio/status":
            self._send_json(self.state.snapshot())
            return
        if request_path == "/test-tone.wav":
            data = generated_tone_wav()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return
        if request_path != "/audio.wav":
            self._send_json(
                {"ok": False, "error": "not found"},
                HTTPStatus.NOT_FOUND,
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "audio/wav")
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(wav_header())
            self.wfile.flush()
            self.state.client_started()
            while True:
                payload = self.state.pop_audio()
                if payload is None:
                    payload = SILENCE_FRAME
                    self.state.note_silence()
                self.wfile.write(payload)
                self.wfile.flush()
                time.sleep(0.02)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            self.state.client_ended()


def self_test() -> int:
    state = UhfAudioState()
    active = struct.pack("<160h", *([2000] * 160))
    state.add_packet(active)
    state.add_packet(b"invalid")
    output = state.pop_audio()
    snapshot = state.snapshot()
    checks = [
        output == active,
        snapshot["packets"] == 2,
        snapshot["accepted_frames"] == 1,
        snapshot["ignored_packets"] == 1,
        wav_header().startswith(b"RIFF"),
        generated_tone_wav().startswith(b"RIFF"),
    ]
    if not all(checks):
        print(json.dumps(snapshot, indent=2))
        print("FINAL: FAIL")
        return 1
    print("PASS: isolated UHF browser audio bridge self-test")
    print("FINAL: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PI-SCANNER isolated UHF browser audio bridge"
    )
    parser.add_argument("--host", default=DEFAULT_HTTP_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    parser.add_argument("--udp-host", default=DEFAULT_UDP_HOST)
    parser.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT)
    parser.add_argument(
        "--max-queue-chunks",
        type=int,
        default=DEFAULT_MAX_QUEUE_CHUNKS,
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    state = UhfAudioState(max_queue_chunks=args.max_queue_chunks)
    receiver = UdpReceiver(state, args.udp_host, args.udp_port)
    receiver.start()

    try:
        httpd = ReusableThreadingHTTPServer(
            (args.host, args.port),
            AudioHandler,
        )
    except OSError as exc:
        print(
            f"FAIL: HTTP bind failed on {args.host}:{args.port}: {exc}",
            flush=True,
        )
        receiver.stop()
        return 1
    httpd.audio_state = state  # type: ignore[attr-defined]

    def request_stop(_signum: int, _frame: Any) -> None:
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    print(
        "PI-SCANNER isolated UHF audio listening "
        f"http={args.host}:{args.port} "
        f"udp={args.udp_host}:{args.udp_port}",
        flush=True,
    )
    try:
        httpd.serve_forever(poll_interval=0.2)
    finally:
        receiver.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
