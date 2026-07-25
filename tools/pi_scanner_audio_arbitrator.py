#!/usr/bin/env python3
"""Select one active audio source from P25, VHF, or UHF without mixing."""

from __future__ import annotations

import argparse
import collections
import json
import math
import select
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Deque

PCM_RATE = 8000
PCM_CHANNELS = 1
PCM_BITS = 16
PCM_FRAME_BYTES = 320
SILENCE_FRAME = bytes(PCM_FRAME_BYTES)


def wav_header() -> bytes:
    byte_rate = PCM_RATE * PCM_CHANNELS * PCM_BITS // 8
    block_align = PCM_CHANNELS * PCM_BITS // 8
    return (
        b"RIFF"
        + struct.pack("<I", 0x7FFFFFFF)
        + b"WAVEfmt "
        + struct.pack(
            "<IHHIIHH",
            16,
            1,
            PCM_CHANNELS,
            PCM_RATE,
            byte_rate,
            block_align,
            PCM_BITS,
        )
        + b"data"
        + struct.pack("<I", 0x7FFFFFFF)
    )


def rms_pcm16(payload: bytes) -> int:
    usable = len(payload) - (len(payload) % 2)
    if usable <= 0:
        return 0
    samples = struct.unpack("<" + "h" * (usable // 2), payload[:usable])
    return int(math.sqrt(sum(value * value for value in samples) / len(samples)))


@dataclass
class SourceState:
    name: str
    port: int
    threshold_rms: int
    packets: int = 0
    active_packets: int = 0
    rejected_packets: int = 0
    last_packet_epoch: float | None = None
    last_active_epoch: float | None = None
    last_rms: int = 0
    warmup_count: int = 0


@dataclass
class ArbitratorState:
    release_seconds: float
    warmup_frames: int
    max_queue_frames: int
    sources: dict[int, SourceState]
    lock: threading.Lock = field(default_factory=threading.Lock)
    queue: Deque[bytes] = field(init=False)
    active_port: int | None = None
    active_since_epoch: float | None = None
    switches: int = 0
    forwarded_frames: int = 0
    silence_frames: int = 0
    clients: int = 0
    started_epoch: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.queue = collections.deque(maxlen=self.max_queue_frames)

    def _release_if_stale(self, now: float) -> None:
        if self.active_port is None:
            return
        source = self.sources[self.active_port]
        if (
            source.last_active_epoch is None
            or now - source.last_active_epoch > self.release_seconds
        ):
            self.active_port = None
            self.active_since_epoch = None
            for item in self.sources.values():
                item.warmup_count = 0
            self.queue.clear()

    def process(self, port: int, payload: bytes, now: float) -> bool:
        if len(payload) != PCM_FRAME_BYTES:
            return False

        with self.lock:
            source = self.sources[port]
            source.packets += 1
            source.last_packet_epoch = now
            source.last_rms = rms_pcm16(payload)
            active = source.last_rms >= source.threshold_rms

            self._release_if_stale(now)

            if self.active_port == port:
                if active:
                    source.last_active_epoch = now
                self.queue.append(payload)
                self.forwarded_frames += 1
                return True

            if not active:
                source.warmup_count = 0
                source.rejected_packets += 1
                return False

            source.active_packets += 1
            source.last_active_epoch = now
            source.warmup_count += 1

            if self.active_port is not None:
                source.rejected_packets += 1
                return False

            if source.warmup_count < self.warmup_frames:
                return False

            self.active_port = port
            self.active_since_epoch = now
            self.switches += 1
            self.queue.clear()
            self.queue.append(payload)
            self.forwarded_frames += 1
            return True

    def pop(self) -> bytes | None:
        with self.lock:
            self._release_if_stale(time.time())
            if self.queue:
                return self.queue.popleft()
            return None

    def snapshot(self) -> dict:
        now = time.time()
        with self.lock:
            self._release_if_stale(now)
            active = (
                self.sources[self.active_port]
                if self.active_port is not None
                else None
            )
            return {
                "ok": True,
                "mode": "three_scanner_audio_arbitrator",
                "active_source": None if active is None else active.name,
                "active_port": self.active_port,
                "active_since_epoch": self.active_since_epoch,
                "active_age_seconds": (
                    None
                    if self.active_since_epoch is None
                    else round(now - self.active_since_epoch, 3)
                ),
                "release_seconds": self.release_seconds,
                "warmup_frames": self.warmup_frames,
                "switches": self.switches,
                "forwarded_frames": self.forwarded_frames,
                "silence_frames": self.silence_frames,
                "clients": self.clients,
                "uptime_seconds": round(now - self.started_epoch, 3),
                "stream_path": "/audio.wav",
                "status_path": "/api/audio/status",
                "sources": {
                    source.name: {
                        "port": source.port,
                        "threshold_rms": source.threshold_rms,
                        "packets": source.packets,
                        "active_packets": source.active_packets,
                        "rejected_packets": source.rejected_packets,
                        "last_rms": source.last_rms,
                        "last_packet_age_seconds": (
                            None
                            if source.last_packet_epoch is None
                            else round(now - source.last_packet_epoch, 3)
                        ),
                        "last_active_age_seconds": (
                            None
                            if source.last_active_epoch is None
                            else round(now - source.last_active_epoch, 3)
                        ),
                        "selected": self.active_port == source.port,
                    }
                    for source in self.sources.values()
                },
            }


class Handler(BaseHTTPRequestHandler):
    server_version = "PiScannerAudioArbitrator/0.1"

    @property
    def state(self) -> ArbitratorState:
        return self.server.audio_state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/api/audio/status"):
            self._json(self.state.snapshot())
            return
        if path != "/audio.wav":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(wav_header())
        self.wfile.flush()

        with self.state.lock:
            self.state.clients += 1
        try:
            while True:
                payload = self.state.pop()
                if payload is None:
                    payload = SILENCE_FRAME
                    with self.state.lock:
                        self.state.silence_frames += 1
                self.wfile.write(payload)
                self.wfile.flush()
                time.sleep(0.02)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with self.state.lock:
                self.state.clients = max(0, self.state.clients - 1)


def receive_loop(state: ArbitratorState, sockets: dict[socket.socket, int]) -> None:
    while True:
        readable, _, _ = select.select(list(sockets), [], [], 0.25)
        now = time.time()
        for sock in readable:
            payload, _address = sock.recvfrom(65535)
            state.process(sockets[sock], payload, now)


def self_test() -> None:
    sources = {
        23456: SourceState("P25", 23456, 25),
        23458: SourceState("VHF", 23458, 550),
        23459: SourceState("UHF", 23459, 550),
    }
    state = ArbitratorState(0.5, 2, 10, sources)
    low = struct.pack("<160h", *([10] * 160))
    high = struct.pack("<160h", *([900] * 160))
    start = time.time()

    assert not state.process(23458, low, start)
    assert not state.process(23458, high, start + 0.01)
    assert state.process(23458, high, start + 0.02)
    assert state.snapshot()["active_source"] == "VHF"
    assert not state.process(23459, high, start + 0.03)
    state.sources[23458].last_active_epoch = start - 1
    assert not state.process(23459, high, start + 0.04)
    assert state.process(23459, high, start + 0.05)
    assert state.snapshot()["active_source"] == "UHF"
    print("PASS: audio arbitrator self-test")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8072)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--p25-port", type=int, default=23456)
    parser.add_argument("--vhf-port", type=int, default=23458)
    parser.add_argument("--uhf-port", type=int, default=23459)
    parser.add_argument("--p25-threshold-rms", type=int, default=25)
    parser.add_argument("--analog-threshold-rms", type=int, default=550)
    parser.add_argument("--release-seconds", type=float, default=1.0)
    parser.add_argument("--warmup-frames", type=int, default=2)
    parser.add_argument("--max-queue-frames", type=int, default=100)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0

    sources = {
        args.p25_port: SourceState(
            "P25", args.p25_port, args.p25_threshold_rms
        ),
        args.vhf_port: SourceState(
            "VHF", args.vhf_port, args.analog_threshold_rms
        ),
        args.uhf_port: SourceState(
            "UHF", args.uhf_port, args.analog_threshold_rms
        ),
    }
    state = ArbitratorState(
        release_seconds=args.release_seconds,
        warmup_frames=max(1, args.warmup_frames),
        max_queue_frames=max(10, args.max_queue_frames),
        sources=sources,
    )

    sockets: dict[socket.socket, int] = {}
    for source in sources.values():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((args.listen_host, source.port))
        sockets[sock] = source.port

    thread = threading.Thread(
        target=receive_loop,
        args=(state, sockets),
        daemon=True,
    )
    thread.start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.audio_state = state  # type: ignore[attr-defined]
    print(
        "Unified scanner audio arbitrator listening "
        f"http://{args.host}:{args.port}/audio.wav",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
