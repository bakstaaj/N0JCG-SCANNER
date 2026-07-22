#!/usr/bin/env python3
"""Select one active OP25 receiver audio stream without mixing.

The native multi_rx runtime emits one UDP stream per RTL serial on ports
23500-23509. This process listens to all pool ports, chooses one non-silent
stream at a time, and forwards it to the existing raw browser-audio bridge on
127.0.0.1:23456.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import select
import signal
import socket
import struct
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PCM_FRAME_BYTES = 320


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def pcm_rms(payload: bytes) -> int:
    if len(payload) != PCM_FRAME_BYTES:
        return 0
    samples = struct.unpack("<160h", payload)
    return int(math.sqrt(sum(sample * sample for sample in samples) / len(samples)))


@dataclass
class SourceStats:
    port: int
    packets: int = 0
    audio_frames: int = 0
    flag_packets: int = 0
    ignored_packets: int = 0
    forwarded_frames: int = 0
    bytes_received: int = 0
    last_packet_utc: float | None = None
    last_audio_utc: float | None = None
    last_non_silent_utc: float | None = None
    last_rms: int = 0
    peak_rms: int = 0


class SourceArbiter:
    def __init__(self, min_rms: int, release_seconds: float) -> None:
        self.min_rms = min_rms
        self.release_seconds = release_seconds
        self.selected_port: int | None = None
        self.selected_since_utc: float | None = None
        self.last_switch_utc: float | None = None
        self.sources: dict[int, SourceStats] = {}

    def source(self, port: int) -> SourceStats:
        if port not in self.sources:
            self.sources[port] = SourceStats(port=port)
        return self.sources[port]

    def tick(self, now: float) -> None:
        if self.selected_port is None:
            return
        selected = self.source(self.selected_port)
        if (
            selected.last_non_silent_utc is None
            or now - selected.last_non_silent_utc > self.release_seconds
        ):
            self.selected_port = None
            self.selected_since_utc = None

    def process_audio(self, port: int, payload: bytes, now: float) -> bool:
        stats = self.source(port)
        rms = pcm_rms(payload)
        stats.audio_frames += 1
        stats.last_audio_utc = now
        stats.last_rms = rms
        stats.peak_rms = max(stats.peak_rms, rms)
        active = rms >= self.min_rms
        if active:
            stats.last_non_silent_utc = now

        self.tick(now)

        if self.selected_port is None:
            if not active:
                return False
            self.selected_port = port
            self.selected_since_utc = now
            self.last_switch_utc = now
            return True

        if self.selected_port == port:
            selected_active = (
                stats.last_non_silent_utc is not None
                and now - stats.last_non_silent_utc <= self.release_seconds
            )
            return active or selected_active

        if not active:
            return False

        selected = self.source(self.selected_port)
        selected_stale = (
            selected.last_non_silent_utc is None
            or now - selected.last_non_silent_utc > self.release_seconds
        )
        if selected_stale:
            self.selected_port = port
            self.selected_since_utc = now
            self.last_switch_utc = now
            return True
        return False


class AudioPool:
    def __init__(
        self,
        *,
        listen_host: str,
        base_port: int,
        port_count: int,
        output_host: str,
        output_port: int,
        state_path: Path,
        min_rms: int,
        release_seconds: float,
    ) -> None:
        self.listen_host = listen_host
        self.base_port = base_port
        self.port_count = port_count
        self.output_host = output_host
        self.output_port = output_port
        self.state_path = state_path
        self.arbiter = SourceArbiter(min_rms=min_rms, release_seconds=release_seconds)
        self.sockets: dict[socket.socket, int] = {}
        self.output_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.running = True
        self.started_utc = time.time()
        self.forwarded_frames = 0
        self.output_errors = 0
        self.bind_errors: list[str] = []
        self.last_state_write = 0.0

    def stop(self, *_args: Any) -> None:
        self.running = False

    def bind(self) -> None:
        for port in range(self.base_port, self.base_port + self.port_count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            try:
                sock.bind((self.listen_host, port))
            except OSError as exc:
                self.bind_errors.append(f"{self.listen_host}:{port}: {exc}")
                sock.close()
                continue
            self.sockets[sock] = port
            self.arbiter.source(port)
        if self.bind_errors or len(self.sockets) != self.port_count:
            raise RuntimeError(
                "unable to bind complete audio pool: " + "; ".join(self.bind_errors)
            )

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        return {
            "ok": not self.bind_errors and len(self.sockets) == self.port_count,
            "mode": "single-active-source-no-mix",
            "listen_host": self.listen_host,
            "base_port": self.base_port,
            "port_count": self.port_count,
            "output_host": self.output_host,
            "output_port": self.output_port,
            "min_rms": self.arbiter.min_rms,
            "release_seconds": self.arbiter.release_seconds,
            "selected_port": self.arbiter.selected_port,
            "selected_since_utc": self.arbiter.selected_since_utc,
            "last_switch_utc": self.arbiter.last_switch_utc,
            "forwarded_frames": self.forwarded_frames,
            "output_errors": self.output_errors,
            "bind_errors": list(self.bind_errors),
            "uptime_seconds": round(now - self.started_utc, 3),
            "sources": {
                str(port): asdict(stats)
                for port, stats in sorted(self.arbiter.sources.items())
            },
            "updated_utc": now,
        }

    def write_state(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_state_write < 1.0:
            return
        atomic_write_json(self.state_path, self.snapshot())
        self.last_state_write = now

    def handle_packet(self, sock: socket.socket, now: float) -> None:
        port = self.sockets[sock]
        try:
            payload, _address = sock.recvfrom(4096)
        except BlockingIOError:
            return
        stats = self.arbiter.source(port)
        stats.packets += 1
        stats.bytes_received += len(payload)
        stats.last_packet_utc = now

        if len(payload) == PCM_FRAME_BYTES:
            if self.arbiter.process_audio(port, payload, now):
                try:
                    self.output_socket.sendto(payload, (self.output_host, self.output_port))
                    stats.forwarded_frames += 1
                    self.forwarded_frames += 1
                except OSError:
                    self.output_errors += 1
            return

        if len(payload) == 2:
            stats.flag_packets += 1
            return

        stats.ignored_packets += 1

    def run(self) -> int:
        self.bind()
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        self.write_state(force=True)
        try:
            while self.running:
                readable, _, _ = select.select(list(self.sockets), [], [], 0.2)
                now = time.time()
                for sock in readable:
                    self.handle_packet(sock, now)
                self.arbiter.tick(now)
                self.write_state()
        finally:
            self.write_state(force=True)
            for sock in self.sockets:
                sock.close()
            self.output_socket.close()
        return 0


def self_test() -> int:
    arbiter = SourceArbiter(min_rms=100, release_seconds=1.0)
    active = struct.pack("<160h", *([1200] * 160))
    silence = b"\x00" * PCM_FRAME_BYTES
    start = 1000.0

    assert arbiter.process_audio(23502, active, start) is True
    assert arbiter.selected_port == 23502
    assert arbiter.process_audio(23503, active, start + 0.1) is False
    assert arbiter.process_audio(23502, silence, start + 0.5) is True
    arbiter.tick(start + 1.2)
    assert arbiter.selected_port is None
    assert arbiter.process_audio(23503, active, start + 1.3) is True
    assert arbiter.selected_port == 23503
    print("AUDIO_POOL_SELF_TEST=PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--base-port", type=int, default=23500)
    parser.add_argument("--port-count", type=int, default=10)
    parser.add_argument("--output-host", default="127.0.0.1")
    parser.add_argument("--output-port", type=int, default=23456)
    parser.add_argument(
        "--state",
        default="/home/pi/PI-P25-SCANNER/runtime/op25/audio_pool_state.json",
    )
    parser.add_argument("--min-rms", type=int, default=100)
    parser.add_argument("--release-seconds", type=float, default=1.0)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.port_count < 1 or args.port_count > 100:
        parser.error("--port-count must be 1..100")
    if not 1 <= args.output_port <= 65535:
        parser.error("--output-port must be 1..65535")

    pool = AudioPool(
        listen_host=args.listen_host,
        base_port=args.base_port,
        port_count=args.port_count,
        output_host=args.output_host,
        output_port=args.output_port,
        state_path=Path(args.state),
        min_rms=max(0, args.min_rms),
        release_seconds=max(0.1, args.release_seconds),
    )
    return pool.run()


if __name__ == "__main__":
    raise SystemExit(main())
