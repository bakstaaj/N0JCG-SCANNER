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
OP25_AUDIO_DRAIN = 0x0000
OP25_AUDIO_DROP = 0x0001


class CaptureRecorder:
    """Bounded raw/event capture at the P25 pool input and output boundary."""

    def __init__(self, directory: Path | None, duration_seconds: float) -> None:
        self.directory = Path(directory) if directory else None
        self.duration_seconds = max(0.0, duration_seconds)
        self.started_utc: float | None = None
        self.deadline_utc: float | None = None
        self.events_handle = None
        self.forwarded_handle = None
        self.input_handles: dict[int, Any] = {}
        self.event_count = 0
        self.input_audio_frames = 0
        self.forwarded_audio_frames = 0
        self.closed = False

    @property
    def enabled(self) -> bool:
        return self.directory is not None and self.duration_seconds > 0

    def start(self, now: float) -> None:
        if not self.enabled or self.started_utc is not None:
            return
        assert self.directory is not None
        self.directory.mkdir(parents=True, exist_ok=True)
        self.started_utc = now
        self.deadline_utc = now + self.duration_seconds
        self.events_handle = (self.directory / "pool_events.jsonl").open(
            "w", encoding="utf-8", buffering=1
        )
        self.forwarded_handle = (self.directory / "pool_forwarded.pcm").open("wb")
        self._write_manifest(completed=False)

    def _write_manifest(self, *, completed: bool) -> None:
        if not self.enabled or self.directory is None:
            return
        atomic_write_json(
            self.directory / "capture_manifest.json",
            {
                "completed": completed,
                "duration_seconds": self.duration_seconds,
                "started_utc": self.started_utc,
                "deadline_utc": self.deadline_utc,
                "event_count": self.event_count,
                "input_audio_frames": self.input_audio_frames,
                "forwarded_audio_frames": self.forwarded_audio_frames,
                "format": {
                    "pcm": "signed 16-bit little-endian mono 8000 Hz",
                    "frame_bytes": PCM_FRAME_BYTES,
                    "events": "newline-delimited JSON",
                },
            },
        )

    def active(self, now: float) -> bool:
        if not self.enabled or self.closed:
            return False
        self.start(now)
        if self.deadline_utc is not None and now >= self.deadline_utc:
            self.close()
            return False
        return True

    def record(
        self,
        *,
        now: float,
        port: int,
        kind: str,
        payload: bytes,
        rms: int | None = None,
        flag: int | None = None,
        selected_before: int | None = None,
        selected_after: int | None = None,
        forwarded: bool = False,
    ) -> None:
        if not self.active(now):
            return
        assert self.directory is not None
        event: dict[str, Any] = {
            "utc": now,
            "offset_seconds": round(now - (self.started_utc or now), 6),
            "port": port,
            "kind": kind,
            "bytes": len(payload),
            "selected_before": selected_before,
            "selected_after": selected_after,
            "forwarded": forwarded,
        }
        if rms is not None:
            event["rms"] = rms
        if flag is not None:
            event["flag"] = flag
        if kind == "audio":
            handle = self.input_handles.get(port)
            if handle is None:
                handle = (self.directory / f"pool_input_{port}.pcm").open("wb")
                self.input_handles[port] = handle
            handle.write(payload)
            self.input_audio_frames += 1
            if forwarded and self.forwarded_handle is not None:
                self.forwarded_handle.write(payload)
                self.forwarded_audio_frames += 1
        if self.events_handle is not None:
            self.events_handle.write(json.dumps(event, separators=(",", ":")) + "\n")
        self.event_count += 1

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for handle in self.input_handles.values():
            handle.close()
        self.input_handles.clear()
        if self.forwarded_handle is not None:
            self.forwarded_handle.close()
            self.forwarded_handle = None
        if self.events_handle is not None:
            self.events_handle.close()
            self.events_handle = None
        self._write_manifest(completed=True)


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
    active_audio_frames: int = 0
    rejected_audio_frames: int = 0
    flag_packets: int = 0
    drain_flags: int = 0
    drop_flags: int = 0
    unknown_flags: int = 0
    boundary_resets: int = 0
    ignored_packets: int = 0
    forwarded_frames: int = 0
    warmup_suppressed_frames: int = 0
    rms_samples: int = 0
    rms_sum: int = 0
    minimum_nonzero_rms: int | None = None
    last_forwarded_rms: int = 0
    bytes_received: int = 0
    last_packet_utc: float | None = None
    last_audio_utc: float | None = None
    last_non_silent_utc: float | None = None
    last_rms: int = 0
    peak_rms: int = 0


class SourceArbiter:
    def __init__(
        self,
        min_rms: int,
        release_seconds: float,
        warmup_frames: int,
    ) -> None:
        self.min_rms = min_rms
        self.release_seconds = release_seconds
        self.warmup_frames = warmup_frames
        self.selected_port: int | None = None
        self.selected_since_utc: float | None = None
        self.last_switch_utc: float | None = None
        self.selected_warmup_remaining = 0
        self.warmup_events = 0
        self.boundary_events = 0
        self.timeout_release_events = 0
        self.sources: dict[int, SourceStats] = {}

    def source(self, port: int) -> SourceStats:
        if port not in self.sources:
            self.sources[port] = SourceStats(port=port)
        return self.sources[port]

    def clear_selection(self) -> None:
        self.selected_port = None
        self.selected_since_utc = None
        self.selected_warmup_remaining = 0

    def end_selection(
        self,
        port: int,
        *,
        boundary: bool = False,
        timeout: bool = False,
    ) -> bool:
        if self.selected_port != port:
            return False
        stats = self.source(port)
        if boundary:
            stats.boundary_resets += 1
            self.boundary_events += 1
        if timeout:
            self.timeout_release_events += 1
        self.clear_selection()
        return True

    def tick(self, now: float) -> None:
        if self.selected_port is None:
            return
        selected = self.source(self.selected_port)
        # Hold a selected receiver through quiet speech and silence as long as
        # OP25 continues delivering PCM. DRAIN/DROP flags are the normal call
        # boundary; this timer is only a lost-packet safety fallback.
        if (
            selected.last_audio_utc is None
            or now - selected.last_audio_utc > self.release_seconds
        ):
            self.end_selection(self.selected_port, timeout=True)

    def process_flag(self, port: int, flag: int, now: float) -> None:
        stats = self.source(port)
        if flag == OP25_AUDIO_DRAIN:
            stats.drain_flags += 1
            self.end_selection(port, boundary=True)
        elif flag == OP25_AUDIO_DROP:
            stats.drop_flags += 1
            self.end_selection(port, boundary=True)
        else:
            stats.unknown_flags += 1

    def begin_selection(self, port: int, now: float) -> None:
        self.selected_port = port
        self.selected_since_utc = now
        self.last_switch_utc = now
        self.selected_warmup_remaining = self.warmup_frames
        self.warmup_events += 1

    def apply_warmup(self, stats: SourceStats) -> bool:
        if self.selected_warmup_remaining <= 0:
            return False
        self.selected_warmup_remaining -= 1
        stats.warmup_suppressed_frames += 1
        return True

    def process_audio(self, port: int, payload: bytes, now: float) -> bool:
        stats = self.source(port)
        rms = pcm_rms(payload)
        stats.audio_frames += 1
        stats.last_audio_utc = now
        stats.last_rms = rms
        stats.peak_rms = max(stats.peak_rms, rms)
        stats.rms_samples += 1
        stats.rms_sum += rms
        if rms > 0:
            if stats.minimum_nonzero_rms is None:
                stats.minimum_nonzero_rms = rms
            else:
                stats.minimum_nonzero_rms = min(
                    stats.minimum_nonzero_rms,
                    rms,
                )
        active = rms >= self.min_rms
        if active:
            stats.active_audio_frames += 1
            stats.last_non_silent_utc = now
        else:
            stats.rejected_audio_frames += 1

        self.tick(now)

        if self.selected_port is None:
            if not active:
                return False
            self.begin_selection(port, now)
            return not self.apply_warmup(stats)

        if self.selected_port == port:
            selected_active = (
                stats.last_non_silent_utc is not None
                and now - stats.last_non_silent_utc <= self.release_seconds
            )
            should_forward = active or selected_active
            if should_forward and self.apply_warmup(stats):
                return False
            return should_forward

        if not active:
            return False

        selected = self.source(self.selected_port)
        selected_stale = (
            selected.last_non_silent_utc is None
            or now - selected.last_non_silent_utc > self.release_seconds
        )
        if selected_stale:
            self.begin_selection(port, now)
            return not self.apply_warmup(stats)
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
        warmup_frames: int,
        capture_dir: Path | None = None,
        capture_seconds: float = 0.0,
    ) -> None:
        self.listen_host = listen_host
        self.base_port = base_port
        self.port_count = port_count
        self.output_host = output_host
        self.output_port = output_port
        self.state_path = state_path
        self.arbiter = SourceArbiter(
            min_rms=min_rms,
            release_seconds=release_seconds,
            warmup_frames=warmup_frames,
        )
        self.sockets: dict[socket.socket, int] = {}
        self.output_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.running = True
        self.started_utc = time.time()
        self.forwarded_frames = 0
        self.output_errors = 0
        self.bind_errors: list[str] = []
        self.last_state_write = 0.0
        self.capture = CaptureRecorder(capture_dir, capture_seconds)

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
            "warmup_frames": self.arbiter.warmup_frames,
            "selected_warmup_remaining": (
                self.arbiter.selected_warmup_remaining
            ),
            "warmup_events": self.arbiter.warmup_events,
            "boundary_events": self.arbiter.boundary_events,
            "timeout_release_events": (
                self.arbiter.timeout_release_events
            ),
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
            "capture": {
                "enabled": self.capture.enabled,
                "directory": str(self.capture.directory) if self.capture.directory else None,
                "started_utc": self.capture.started_utc,
                "deadline_utc": self.capture.deadline_utc,
                "closed": self.capture.closed,
                "event_count": self.capture.event_count,
                "input_audio_frames": self.capture.input_audio_frames,
                "forwarded_audio_frames": self.capture.forwarded_audio_frames,
            },
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
        selected_before = self.arbiter.selected_port

        if len(payload) == PCM_FRAME_BYTES:
            should_forward = self.arbiter.process_audio(port, payload, now)
            forwarded = False
            if should_forward:
                try:
                    self.output_socket.sendto(payload, (self.output_host, self.output_port))
                    stats.forwarded_frames += 1
                    stats.last_forwarded_rms = stats.last_rms
                    self.forwarded_frames += 1
                    forwarded = True
                except OSError:
                    self.output_errors += 1
            self.capture.record(
                now=now,
                port=port,
                kind="audio",
                payload=payload,
                rms=stats.last_rms,
                selected_before=selected_before,
                selected_after=self.arbiter.selected_port,
                forwarded=forwarded,
            )
            return

        if len(payload) == 2:
            stats.flag_packets += 1
            flag = struct.unpack("<H", payload)[0]
            self.arbiter.process_flag(port, flag, now)
            self.capture.record(
                now=now,
                port=port,
                kind="flag",
                payload=payload,
                flag=flag,
                selected_before=selected_before,
                selected_after=self.arbiter.selected_port,
            )
            return

        stats.ignored_packets += 1
        self.capture.record(
            now=now,
            port=port,
            kind="ignored",
            payload=payload,
            selected_before=selected_before,
            selected_after=self.arbiter.selected_port,
        )

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
                self.capture.active(now)
                self.write_state()
        finally:
            self.capture.close()
            self.write_state(force=True)
            for sock in self.sockets:
                sock.close()
            self.output_socket.close()
        return 0


def self_test() -> int:
    arbiter = SourceArbiter(
        min_rms=25,
        release_seconds=1.0,
        warmup_frames=2,
    )
    active = struct.pack("<160h", *([56] * 160))
    below = struct.pack("<160h", *([24] * 160))
    threshold = struct.pack("<160h", *([25] * 160))
    silence = b"\x00" * PCM_FRAME_BYTES
    start = 1000.0

    assert arbiter.process_audio(23502, below, start) is False
    assert arbiter.selected_port is None
    assert arbiter.process_audio(23502, threshold, start + 0.05) is False
    assert arbiter.selected_port == 23502
    assert arbiter.process_audio(23502, active, start + 0.1) is False
    assert arbiter.process_audio(23502, active, start + 0.15) is True
    assert arbiter.source(23502).warmup_suppressed_frames == 2
    assert arbiter.selected_port == 23502
    assert arbiter.process_audio(23503, active, start + 0.1) is False
    assert arbiter.process_audio(23502, silence, start + 0.5) is True
    arbiter.tick(start + 1.2)
    assert arbiter.selected_port == 23502
    assert arbiter.timeout_release_events == 0

    arbiter.process_flag(23502, OP25_AUDIO_DRAIN, start + 1.21)
    assert arbiter.selected_port is None
    assert arbiter.source(23502).drain_flags == 1
    assert arbiter.source(23502).boundary_resets == 1
    assert arbiter.boundary_events == 1

    assert arbiter.process_audio(23503, active, start + 1.3) is False
    assert arbiter.selected_port == 23503
    assert arbiter.process_audio(23503, active, start + 1.35) is False
    assert arbiter.process_audio(23503, active, start + 1.4) is True
    assert arbiter.source(23503).warmup_suppressed_frames == 2
    assert arbiter.warmup_events == 2

    arbiter.process_flag(23503, OP25_AUDIO_DROP, start + 1.41)
    assert arbiter.selected_port is None
    assert arbiter.source(23503).drop_flags == 1
    assert arbiter.boundary_events == 2

    assert arbiter.process_audio(23503, active, start + 1.5) is False
    arbiter.tick(start + 4.1)
    assert arbiter.selected_port is None
    assert arbiter.timeout_release_events == 1

    arbiter.process_flag(23503, 0x1234, start + 4.2)
    assert arbiter.source(23503).unknown_flags == 1
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
    parser.add_argument("--min-rms", type=int, default=25)
    parser.add_argument("--release-seconds", type=float, default=1.0)
    parser.add_argument("--warmup-frames", type=int, default=0)
    parser.add_argument("--capture-dir", default="")
    parser.add_argument("--capture-seconds", type=float, default=0.0)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.port_count < 1 or args.port_count > 100:
        parser.error("--port-count must be 1..100")
    if not 1 <= args.output_port <= 65535:
        parser.error("--output-port must be 1..65535")
    if args.warmup_frames < 0 or args.warmup_frames > 50:
        parser.error("--warmup-frames must be 0..50")

    pool = AudioPool(
        listen_host=args.listen_host,
        base_port=args.base_port,
        port_count=args.port_count,
        output_host=args.output_host,
        output_port=args.output_port,
        state_path=Path(args.state),
        min_rms=max(0, args.min_rms),
        release_seconds=max(0.1, args.release_seconds),
        warmup_frames=args.warmup_frames,
        capture_dir=Path(args.capture_dir) if args.capture_dir else None,
        capture_seconds=args.capture_seconds,
    )
    return pool.run()


if __name__ == "__main__":
    raise SystemExit(main())
