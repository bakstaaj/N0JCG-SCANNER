#!/usr/bin/env python3
# PI-SCANNER source-aware UDP PCM to browser WAV audio arbiter.

from __future__ import annotations

import argparse
import array
import json
import math
import signal
import socket
import struct
import sys
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
DEFAULT_HTTP_PORT = 8072
DEFAULT_UDP_HOST = "127.0.0.1"
DEFAULT_P25_PORT = 23456
DEFAULT_ANALOG_2M_PORT = 23458
DEFAULT_ANALOG_70CM_PORT = 23459
DEFAULT_RELEASE_SECONDS = 0.75
DEFAULT_ACTIVITY_RMS = 120
DEFAULT_MAX_QUEUE_CHUNKS = 1500
DEFAULT_ACQUISITION_GRACE_SECONDS = 0.040
DEFAULT_SOURCE_PRIORITIES = {
    "p25_voice": 400,
    "p25_control": 350,
    "analog_2m": 200,
    "analog_70cm": 100,
}


def pcm_rms(frame: bytes) -> int:
    usable = len(frame) - (len(frame) % 2)
    if usable <= 0:
        return 0
    values = array.array("h")
    values.frombytes(frame[:usable])
    if sys.byteorder != "little":
        values.byteswap()
    if not values:
        return 0
    return int(math.sqrt(sum(int(v) * int(v) for v in values) / len(values)))


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


def generated_tone_wav(seconds: float = 1.0, frequency_hz: float = 880.0) -> bytes:
    frames = bytearray()
    for n in range(int(PCM_RATE_HZ * seconds)):
        sample = int(
            math.sin((2.0 * math.pi * frequency_hz * n) / PCM_RATE_HZ) * 12000
        )
        frames.extend(struct.pack("<h", sample))
    data_size = len(frames)
    header = b"".join(
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
                PCM_RATE_HZ * PCM_CHANNELS * PCM_BITS // 8,
                PCM_CHANNELS * PCM_BITS // 8,
                PCM_BITS,
            ),
            b"data",
            struct.pack("<I", data_size),
        ]
    )
    return header + bytes(frames)


@dataclass
class SourceStats:
    name: str
    port: int
    priority: int = 0
    packets: int = 0
    audio_packets: int = 0
    flag_packets: int = 0
    ignored_packets: int = 0
    accepted_frames: int = 0
    dropped_non_owner_frames: int = 0
    silent_frames: int = 0
    bytes_received: int = 0
    last_packet_utc: float | None = None
    last_audio_utc: float | None = None
    last_active_utc: float | None = None
    last_rms: int = 0
    peak_rms: int = 0


@dataclass
class AudioArbiterState:
    release_seconds: float = DEFAULT_RELEASE_SECONDS
    activity_rms: int = DEFAULT_ACTIVITY_RMS
    max_queue_chunks: int = DEFAULT_MAX_QUEUE_CHUNKS
    acquisition_grace_seconds: float = DEFAULT_ACQUISITION_GRACE_SECONDS
    source_priorities: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_SOURCE_PRIORITIES)
    )
    queue: deque[bytes] = field(init=False)
    pending_frames: dict[str, deque[bytes]] = field(init=False)
    pending_since_utc: float | None = None
    sources: dict[str, SourceStats] = field(default_factory=dict)
    active_source: str | None = None
    active_since_utc: float | None = None
    source_switches: int = 0
    chunks_sent: int = 0
    silence_chunks_sent: int = 0
    stream_clients: int = 0
    underruns: int = 0
    started_utc: float = field(default_factory=time.time)
    last_sent_utc: float | None = None
    bind_errors: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    # PHASE10_UNIFIED_ACTIVITY_ARBITRATION_V0_6I
    def __post_init__(self) -> None:
        self.queue = deque(maxlen=self.max_queue_chunks)
        pending_limit = max(
            4,
            int(round(self.acquisition_grace_seconds / 0.02)) + 4,
        )
        self.pending_frames = {}
        self._pending_limit = pending_limit

    def register_source(self, name: str, port: int) -> None:
        with self.lock:
            priority = int(self.source_priorities.get(name, 0))
            self.sources[name] = SourceStats(
                name=name,
                port=port,
                priority=priority,
            )
            self.pending_frames[name] = deque(maxlen=self._pending_limit)

    def _clear_pending_locked(self) -> None:
        self.pending_since_utc = None
        for frames in self.pending_frames.values():
            frames.clear()

    def _commit_pending_locked(
        self,
        now: float,
        force: bool = False,
    ) -> None:
        if self.active_source is not None or self.pending_since_utc is None:
            return
        elapsed = now - self.pending_since_utc
        if not force and elapsed < self.acquisition_grace_seconds:
            return

        candidates = [
            name
            for name, frames in self.pending_frames.items()
            if frames
        ]
        if not candidates:
            self._clear_pending_locked()
            return

        winner = max(
            candidates,
            key=lambda name: (
                int(self.sources[name].priority),
                -candidates.index(name),
            ),
        )
        self.active_source = winner
        self.active_since_utc = self.pending_since_utc
        self.source_switches += 1
        self.queue.clear()

        for name in candidates:
            frames = self.pending_frames[name]
            if name == winner:
                self.sources[name].accepted_frames += len(frames)
                self.queue.extend(frames)
            else:
                self.sources[name].dropped_non_owner_frames += len(frames)
        self._clear_pending_locked()

    def _release_if_stale_locked(self, now: float) -> None:
        if self.active_source is None:
            return
        source = self.sources.get(self.active_source)
        last_active = source.last_active_utc if source else None
        if last_active is None or now - last_active > self.release_seconds:
            self.active_source = None
            self.active_since_utc = None
            self.queue.clear()
            self._clear_pending_locked()

    def add_packet(self, source_name: str, payload: bytes) -> None:
        now = time.time()
        with self.lock:
            source = self.sources[source_name]
            source.packets += 1
            source.bytes_received += len(payload)
            source.last_packet_utc = now

            if len(payload) == 2 and source_name.startswith("p25"):
                source.flag_packets += 1
                return
            if len(payload) != AUDIO_FRAME_BYTES:
                source.ignored_packets += 1
                return

            source.audio_packets += 1
            source.last_audio_utc = now
            rms = pcm_rms(payload)
            source.last_rms = rms
            source.peak_rms = max(source.peak_rms, rms)
            self._release_if_stale_locked(now)

            if rms < self.activity_rms:
                source.silent_frames += 1
                return

            source.last_active_utc = now
            if self.active_source is None:
                if self.pending_since_utc is None:
                    self.pending_since_utc = now
                self.pending_frames[source_name].append(payload)
                self._commit_pending_locked(now)
                return

            if self.active_source == source_name:
                source.accepted_frames += 1
                self.queue.append(payload)
            else:
                source.dropped_non_owner_frames += 1

    def pop_audio(self) -> bytes | None:
        now = time.time()
        with self.lock:
            self._release_if_stale_locked(now)
            self._commit_pending_locked(now)
            if not self.queue:
                self.underruns += 1
                return None
            self.chunks_sent += 1
            self.last_sent_utc = now
            return self.queue.popleft()

    def note_silence(self) -> None:
        with self.lock:
            self.silence_chunks_sent += 1
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
            self._release_if_stale_locked(now)
            self._commit_pending_locked(now)
            return {
                "ok": not bool(self.bind_errors),
                "mode": "current-transmission-wins-priority-tiebreak-v0.6i",
                "preemption_enabled": False,
                "active_source": self.active_source,
                "active_since_utc": self.active_since_utc,
                "release_seconds": self.release_seconds,
                "acquisition_grace_ms": round(
                    self.acquisition_grace_seconds * 1000.0,
                    3,
                ),
                "source_priority_order": [
                    name
                    for name, _priority in sorted(
                        self.source_priorities.items(),
                        key=lambda item: (-int(item[1]), item[0]),
                    )
                ],
                "pending_sources": [
                    name
                    for name, frames in self.pending_frames.items()
                    if frames
                ],
                "activity_rms": self.activity_rms,
                "source_switches": self.source_switches,
                "queued_chunks": len(self.queue),
                "chunks_sent": self.chunks_sent,
                "silence_chunks_sent": self.silence_chunks_sent,
                "stream_clients": self.stream_clients,
                "underruns": self.underruns,
                "uptime_seconds": round(now - self.started_utc, 3),
                "last_sent_age_seconds": (
                    None
                    if self.last_sent_utc is None
                    else round(now - self.last_sent_utc, 3)
                ),
                "sources": {
                    name: {
                        **vars(source),
                        "last_packet_age_seconds": (
                            None
                            if source.last_packet_utc is None
                            else round(now - source.last_packet_utc, 3)
                        ),
                        "last_audio_age_seconds": (
                            None
                            if source.last_audio_utc is None
                            else round(now - source.last_audio_utc, 3)
                        ),
                        "last_active_age_seconds": (
                            None
                            if source.last_active_utc is None
                            else round(now - source.last_active_utc, 3)
                        ),
                    }
                    for name, source in self.sources.items()
                },
                "bind_errors": list(self.bind_errors),
                "stream_path": "/audio.wav",
                "test_tone_path": "/test-tone.wav",
            }


class UdpReceiver(threading.Thread):
    def __init__(
        self,
        state: AudioArbiterState,
        source_name: str,
        host: str,
        port: int,
    ) -> None:
        super().__init__(daemon=True)
        self.state = state
        self.source_name = source_name
        self.host = host
        self.port = port
        self.keep_running = True
        self.sock: socket.socket | None = None
        self.state.register_source(source_name, port)

    def run(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.5)
            sock.bind((self.host, self.port))
            self.sock = sock
        except OSError as exc:
            with self.state.lock:
                self.state.bind_errors.append(
                    f"{self.source_name} {self.host}:{self.port}: {exc}"
                )
            return
        while self.keep_running:
            try:
                payload, _addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            self.state.add_packet(self.source_name, payload)

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
    server_version = "PiScannerAudioArbiter/0.6B"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    @property
    def state(self) -> AudioArbiterState:
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

    def do_GET(self) -> None:
        if self.path == "/api/audio/status":
            self._send_json(self.state.snapshot())
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
    state = AudioArbiterState(
        release_seconds=0.05,
        activity_rms=100,
        acquisition_grace_seconds=0.02,
    )
    for name, port in (
        ("p25_control", 23456),
        ("p25_voice", 23457),
        ("analog_2m", 23458),
        ("analog_70cm", 23459),
    ):
        state.register_source(name, port)
    active = array.array("h", [2000] * 160)
    if sys.byteorder != "little":
        active.byteswap()
    active_frame = active.tobytes()

    # Analog arrives first, but P25 arrives inside the acquisition window.
    # The deterministic priority tie-break must select P25.
    state.add_packet("analog_2m", active_frame)
    state.add_packet("p25_control", active_frame)
    time.sleep(0.025)
    first = state.snapshot()
    if first["active_source"] != "p25_control":
        print("FAIL: priority tie-break did not select P25")
        return 1
    if first["preemption_enabled"] is not False:
        print("FAIL: arbiter unexpectedly reports preemption")
        return 1

    # An established source must not be preempted.
    state.add_packet("p25_control", active_frame)
    state.add_packet("p25_voice", active_frame)
    held = state.snapshot()
    if held["active_source"] != "p25_control":
        print("FAIL: established transmission was preempted")
        return 1

    time.sleep(0.06)
    state.add_packet("analog_2m", active_frame)
    time.sleep(0.025)
    second = state.snapshot()
    if second["active_source"] != "analog_2m":
        print("FAIL: stale source did not release to analog")
        return 1
    if second["sources"]["analog_2m"]["accepted_frames"] < 1:
        print("FAIL: analog accepted-frame count")
        return 1
    print("PASS: source-aware priority tie-break self-test")
    print("FINAL: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PI-SCANNER browser audio arbiter")
    parser.add_argument("--host", default=DEFAULT_HTTP_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    parser.add_argument("--udp-host", default=DEFAULT_UDP_HOST)
    parser.add_argument("--udp-port", type=int, default=DEFAULT_P25_PORT)
    parser.add_argument("--analog-2m-port", type=int, default=DEFAULT_ANALOG_2M_PORT)
    parser.add_argument("--analog-70cm-port", type=int, default=DEFAULT_ANALOG_70CM_PORT)
    parser.add_argument("--release-seconds", type=float, default=DEFAULT_RELEASE_SECONDS)
    parser.add_argument(
        "--acquisition-grace-ms",
        type=float,
        default=DEFAULT_ACQUISITION_GRACE_SECONDS * 1000.0,
    )
    parser.add_argument("--activity-rms", type=int, default=DEFAULT_ACTIVITY_RMS)
    parser.add_argument("--max-queue-chunks", type=int, default=DEFAULT_MAX_QUEUE_CHUNKS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    state = AudioArbiterState(
        release_seconds=max(0.1, args.release_seconds),
        activity_rms=max(0, args.activity_rms),
        max_queue_chunks=max(100, args.max_queue_chunks),
        acquisition_grace_seconds=max(
            0.0,
            min(0.25, args.acquisition_grace_ms / 1000.0),
        ),
    )
    sources = (
        ("p25_control", args.udp_port),
        ("p25_voice", args.udp_port + 1),
        ("analog_2m", args.analog_2m_port),
        ("analog_70cm", args.analog_70cm_port),
    )
    receivers = [
        UdpReceiver(state, name, args.udp_host, port)
        for name, port in sources
    ]
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
        "PI-SCANNER browser audio arbiter listening "
        f"http={args.host}:{args.port} "
        + " ".join(f"{name}={args.udp_host}:{port}" for name, port in sources),
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
