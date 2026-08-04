#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import select
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Deque

RATE = 8000
FRAME_BYTES = 320
FRAME_SECONDS = FRAME_BYTES / (RATE * 2)
SILENCE = bytes(FRAME_BYTES)


def wav_header() -> bytes:
    return (
        b"RIFF" + struct.pack("<I", 0x7FFFFFFF) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, RATE, RATE * 2, 2, 16)
        + b"data" + struct.pack("<I", 0x7FFFFFFF)
    )


@dataclass
class Source:
    name: str
    port: int
    packets: int = 0
    forwarded: int = 0
    rejected: int = 0
    last_packet: float | None = None


@dataclass
class State:
    release_seconds: float
    warmup_frames: int
    prebuffer_frames: int
    max_queue_frames: int
    sources: dict[int, Source]
    lock: threading.Lock = field(default_factory=threading.Lock)
    condition: threading.Condition = field(init=False)
    queue: Deque[tuple[int, bytes]] = field(init=False)
    next_sequence: int = 0
    active_port: int | None = None
    active_since: float | None = None
    warmup_port: int | None = None
    warmup_count: int = 0
    playback_started: bool = False
    switches: int = 0
    silence_frames: int = 0
    clients: int = 0
    started: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.condition = threading.Condition(self.lock)
        self.queue = collections.deque(maxlen=self.max_queue_frames)

    def _release_stale(self, now: float) -> None:
        if self.active_port is None:
            return
        source = self.sources[self.active_port]
        if source.last_packet is None or now - source.last_packet > self.release_seconds:
            self.active_port = None
            self.active_since = None
            self.playback_started = False
            self.warmup_port = None
            self.warmup_count = 0
            self.queue.clear()

    def process(self, port: int, payload: bytes, now: float) -> bool:
        if len(payload) != FRAME_BYTES:
            return False
        with self.condition:
            source = self.sources[port]
            source.packets += 1
            source.last_packet = now
            self._release_stale(now)

            if self.active_port is None:
                if self.warmup_port != port:
                    self.warmup_port = port
                    self.warmup_count = 0
                self.warmup_count += 1
                if self.warmup_count < self.warmup_frames:
                    return False
                self.active_port = port
                self.active_since = now
                self.playback_started = False
                self.switches += 1
                self.queue.clear()

            if self.active_port != port:
                source.rejected += 1
                return False

            self.queue.append((self.next_sequence, payload))
            self.next_sequence += 1
            source.forwarded += 1
            self.condition.notify_all()
            return True

    def register_client(self) -> int:
        with self.lock:
            self.clients += 1
            if not self.queue:
                return self.next_sequence
            oldest_sequence = self.queue[0][0]
            return max(
                oldest_sequence,
                self.next_sequence - self.prebuffer_frames,
            )

    def unregister_client(self) -> None:
        with self.lock:
            self.clients = max(0, self.clients - 1)

    def read_after(
        self,
        sequence: int,
        wait_seconds: float = 0.15,
    ) -> tuple[bytes | None, int]:
        """Return one frame without consuming it for any other browser."""
        deadline = time.time() + wait_seconds
        with self.condition:
            while True:
                self._release_stale(time.time())
                if self.active_port is None:
                    self.playback_started = False
                    return None, sequence
                if not self.playback_started:
                    if len(self.queue) >= self.prebuffer_frames:
                        self.playback_started = True
                    else:
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            return None, sequence
                        self.condition.wait(remaining)
                        continue
                if self.queue:
                    oldest_sequence = self.queue[0][0]
                    if sequence < oldest_sequence:
                        sequence = oldest_sequence
                    if sequence < self.next_sequence:
                        offset = sequence - oldest_sequence
                        if 0 <= offset < len(self.queue):
                            frame_sequence, frame = self.queue[offset]
                            return frame, frame_sequence + 1
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None, sequence
                self.condition.wait(remaining)

    def snapshot(self) -> dict:
        now = time.time()
        with self.lock:
            self._release_stale(now)
            active = self.sources.get(self.active_port)
            return {
                "ok": True,
                "mode": "three_scanner_audio_arbitrator_v3_fanout",
                "active_source": active.name if active else None,
                "active_port": self.active_port,
                "release_seconds": self.release_seconds,
                "warmup_frames": self.warmup_frames,
                "prebuffer_frames": self.prebuffer_frames,
                "queue_frames": len(self.queue),
                "playback_started": self.playback_started,
                "switches": self.switches,
                "silence_frames": self.silence_frames,
                "clients": self.clients,
                "uptime_seconds": round(now - self.started, 3),
                "sources": {
                    source.name: {
                        "port": source.port,
                        "packets": source.packets,
                        "forwarded": source.forwarded,
                        "rejected": source.rejected,
                        "selected": self.active_port == source.port,
                        "last_packet_age_seconds": None if source.last_packet is None else round(now - source.last_packet, 3),
                    }
                    for source in self.sources.values()
                },
            }


class Handler(BaseHTTPRequestHandler):
    server_version = "PiScannerAudioArbitrator/0.3"

    @property
    def state(self) -> State:
        return self.server.audio_state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/api/audio/status"):
            payload = json.dumps(self.state.snapshot(), indent=2).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
            return
        if path not in ("/audio.wav", "/audio.pcm"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            "audio/wav" if path == "/audio.wav" else "application/octet-stream",
        )
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if path == "/audio.wav":
            self.wfile.write(wav_header())
            self.wfile.flush()
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        sequence = self.state.register_client()
        next_send = time.monotonic()
        try:
            while True:
                next_send += FRAME_SECONDS
                frame, sequence = self.state.read_after(
                    sequence,
                    wait_seconds=max(0.0, next_send - time.monotonic()),
                )
                if frame is None:
                    frame = SILENCE
                    with self.state.lock:
                        self.state.silence_frames += 1
                delay = next_send - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -(FRAME_SECONDS * 2):
                    next_send = time.monotonic()
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self.state.unregister_client()


def receive_loop(state: State, sockets: dict[socket.socket, int]) -> None:
    while True:
        readable, _, _ = select.select(list(sockets), [], [], 0.25)
        now = time.time()
        for sock in readable:
            payload, _ = sock.recvfrom(65535)
            state.process(sockets[sock], payload, now)


def self_test() -> None:
    state = State(
        0.5,
        2,
        3,
        20,
        {
            23456: Source("P25", 23456),
            23458: Source("VHF", 23458),
            23459: Source("UHF", 23459),
        },
    )
    frame = bytes(FRAME_BYTES)
    t = time.time()
    assert not state.process(23458, frame, t)
    assert state.process(23458, frame, t + 0.01)
    assert state.snapshot()["active_source"] == "VHF"
    assert not state.process(23459, frame, t + 0.02)
    state.sources[23458].last_packet = t - 1
    assert not state.process(23459, frame, t + 0.03)
    assert state.process(23459, frame, t + 0.04)
    assert state.snapshot()["active_source"] == "UHF"
    print("PASS: audio arbitrator v3 fan-out self-test")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8072)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--p25-port", type=int, default=23456)
    parser.add_argument("--vhf-port", type=int, default=23458)
    parser.add_argument("--uhf-port", type=int, default=23459)
    parser.add_argument("--release-seconds", type=float, default=1.5)
    parser.add_argument("--warmup-frames", type=int, default=2)
    parser.add_argument("--prebuffer-frames", type=int, default=3)
    parser.add_argument("--max-queue-frames", type=int, default=100)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    state = State(
        args.release_seconds,
        args.warmup_frames,
        args.prebuffer_frames,
        args.max_queue_frames,
        {
            args.p25_port: Source("P25", args.p25_port),
            args.vhf_port: Source("VHF", args.vhf_port),
            args.uhf_port: Source("UHF", args.uhf_port),
        },
    )
    sockets: dict[socket.socket, int] = {}
    for source in state.sources.values():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((args.listen_host, source.port))
        sockets[sock] = source.port

    threading.Thread(target=receive_loop, args=(state, sockets), daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.audio_state = state  # type: ignore[attr-defined]
    print(
        f"Unified scanner audio arbitrator v3 fan-out listening "
        f"http://{args.host}:{args.port}/audio.wav",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
