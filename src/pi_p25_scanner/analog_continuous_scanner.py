#!/usr/bin/env python3
"""Continuous serial-bound analog scanner for PI-SCANNER."""

from __future__ import annotations

import argparse
import array
import collections
import csv
import json
import math
import os
import select
import signal
import socket
import statistics
import subprocess
import tempfile
import sys
import time
from pathlib import Path
from typing import Any, Deque

ROLE_DEFAULTS = {
    "analog_2m": {
        "serial": "00000144",
        "udp_port": 23458,
        "status_name": "analog_2m.json",
        "expected_channels": 32,
    },
    "analog_70cm": {
        "serial": "00000440",
        "udp_port": 23459,
        "status_name": "analog_70cm.json",
        "expected_channels": 7,
    },
}

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "runtime/settings/analog_receivers.json"
TEMPLATE_CONFIG = ROOT / "config/analog_receivers.example.json"
DEFAULT_STATUS_DIR = ROOT / "runtime/status"

DEMOD_RATE = 24000
AUDIO_RATE = 8000
DECIMATION = DEMOD_RATE // AUDIO_RATE
FRAME_MS = 20
DEMOD_FRAME_SAMPLES = DEMOD_RATE * FRAME_MS // 1000
DEMOD_FRAME_BYTES = DEMOD_FRAME_SAMPLES * 2
AUDIO_FRAME_SAMPLES = AUDIO_RATE * FRAME_MS // 1000
AUDIO_FRAME_BYTES = AUDIO_FRAME_SAMPLES * 2
TRANSIENT_PATTERNS = (
    "usb_claim_interface error -6",
    "failed to open rtlsdr device",
    "no supported devices found",
    "device or resource busy",
    "resource busy",
)


class ScannerError(RuntimeError):
    pass


def rms_pcm16(data: bytes) -> int:
    usable = len(data) - (len(data) % 2)
    samples = array.array("h")
    samples.frombytes(data[:usable])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0
    return int(math.sqrt(sum(int(v) * int(v) for v in samples) / len(samples)))


def downsample_pcm16_3x(data: bytes) -> bytes:
    usable = len(data) - (len(data) % 6)
    samples = array.array("h")
    samples.frombytes(data[:usable])
    if sys.byteorder != "little":
        samples.byteswap()
    output = array.array("h")
    for index in range(0, len(samples), 3):
        output.append(int((int(samples[index]) + int(samples[index + 1]) + int(samples[index + 2])) / 3))
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_config(
    path: Path,
    role: str,
    template_path: Path = TEMPLATE_CONFIG,
) -> tuple[dict[str, Any], Path]:
    source = path if path.exists() else template_path
    if not source.exists():
        raise ScannerError(f"configuration missing: {path} and {template_path}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    worker = payload["workers"][role]
    expected = ROLE_DEFAULTS[role]
    serial = str(worker.get("rtl_serial") or "")
    if serial != expected["serial"]:
        raise ScannerError(
            f"{role} must use serial {expected['serial']}; configured {serial!r}"
        )
    channels = [
        dict(channel)
        for channel in worker.get("channels", [])
        if isinstance(channel, dict) and channel.get("enabled", True)
    ]
    if not channels:
        raise ScannerError(f"{role} has no enabled channels")
    worker = dict(worker)
    worker["channels"] = sorted(
        channels,
        key=lambda item: (
            -int(item.get("priority") or 0),
            int(item.get("frequency_hz") or 0),
        ),
    )
    return worker, source


def rtl_fm_command(
    serial: str,
    channel: dict[str, Any],
    worker: dict[str, Any],
) -> list[str]:
    mode_text = str(channel.get("mode") or "fm").strip().lower()
    mode = "am" if mode_text == "am" else "fm"
    gain = float(channel.get("gain_db") or worker.get("gain_db") or 49.6)
    ppm = int(worker.get("ppm") or 0)
    command = [
        "rtl_fm",
        "-d", serial,
        "-f", str(int(channel["frequency_hz"])),
        "-M", mode,
        "-s", str(int(worker.get("demod_rate_hz") or DEMOD_RATE)),
        "-g", str(gain),
        "-l", "0",
        "-p", str(ppm),
        "-E", "dc",
    ]
    if mode == "fm":
        command += ["-E", "deemp"]
    return command


def build_spectrum_segments(frequencies_hz, max_gap_hz, edge_padding_hz, minimum_span_hz):
    frequencies = sorted(set(int(v) for v in frequencies_hz))
    if not frequencies:
        return []
    groups = [[frequencies[0]]]
    for frequency_hz in frequencies[1:]:
        if frequency_hz - groups[-1][-1] > max_gap_hz:
            groups.append([frequency_hz])
        else:
            groups[-1].append(frequency_hz)
    segments = []
    for group in groups:
        low_hz = min(group) - edge_padding_hz
        high_hz = max(group) + edge_padding_hz
        if high_hz - low_hz < minimum_span_hz:
            midpoint = (low_hz + high_hz) // 2
            half = minimum_span_hz // 2
            low_hz = midpoint - half
            high_hz = midpoint + half
        segments.append({
            "low_hz": max(100000, int(low_hz)),
            "high_hz": int(high_hz),
            "frequencies_hz": list(group),
        })
    return segments


def parse_rtl_power_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for raw in csv.reader(handle):
            if len(raw) < 7:
                continue
            try:
                low_hz = float(raw[2])
                high_hz = float(raw[3])
                step_hz = float(raw[4])
                sample_count = int(float(raw[5]))
                powers = [float(value) for value in raw[6:] if str(value).strip()]
            except (TypeError, ValueError):
                continue
            if not powers or step_hz <= 0:
                continue
            rows.append({
                "low_hz": low_hz,
                "high_hz": high_hz,
                "step_hz": step_hz,
                "sample_count": sample_count,
                "powers_db": powers,
                "noise_floor_db": float(statistics.median(powers)),
            })
    return rows


def spectrum_power_at(
    rows: list[dict[str, Any]],
    frequency_hz: int,
) -> tuple[float, float] | None:
    target = float(frequency_hz)
    for row in rows:
        if row["low_hz"] <= target <= row["high_hz"] + row["step_hz"]:
            index = int(round((target - row["low_hz"]) / row["step_hz"]))
            powers = row["powers_db"]
            if 0 <= index < len(powers):
                return float(powers[index]), float(row["noise_floor_db"])
    return None


class ContinuousScanner:
    def __init__(
        self,
        role: str,
        config_path: Path,
        status_path: Path,
        no_forward: bool = False,
    ) -> None:
        self.role = role
        self.defaults = ROLE_DEFAULTS[role]
        self.worker, self.config_source = load_config(config_path, role)
        self.serial = self.defaults["serial"]
        self.status_path = status_path
        self.no_forward = no_forward
        self.stop_requested = False
        self.process: subprocess.Popen[bytes] | None = None
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_target = (
            str(self.worker.get("audio_udp_host") or "127.0.0.1"),
            int(self.worker.get("audio_udp_port") or self.defaults["udp_port"]),
        )
        self.channel_tunes = 0
        self.scan_cycles = 0
        self.lock_count = 0
        self.watchdog_timeouts = 0
        self.child_restarts = 0
        self.spectrum_sweeps = 0
        self.spectrum_failures = 0
        self.spectrum_candidates_total = 0
        self.last_spectrum: dict[str, Any] | None = None
        self.frames_received = 0
        self.bytes_received = 0
        self.frames_forwarded = 0
        self.last_lock: dict[str, Any] | None = None
        self.last_rms = 0
        self.last_baseline_rms = 0
        self.last_threshold_rms = 0
        self.last_active_frames = 0
        self.started_monotonic = time.monotonic()

    def request_stop(self, _signum: int, _frame: Any) -> None:
        self.stop_requested = True
        self.stop_process()

    def stop_process(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGINT)
                self.process.wait(timeout=2.0)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        self.process = None

    def status(
        self,
        state: str,
        channel: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        payload: dict[str, Any] = {
            "ok": state not in {"error"},
            "role": self.role,
            "state": state,
            "rtl_serial": self.serial,
            "config_source": str(self.config_source),
            "channel_count": len(self.worker["channels"]),
            "channel_tunes": self.channel_tunes,
            "scan_cycles": self.scan_cycles,
            "lock_count": self.lock_count,
            "watchdog_timeouts": self.watchdog_timeouts,
            "child_restarts": self.child_restarts,
            "search_mode": str(self.worker.get("search_mode") or "linear"),
            "spectrum_sweeps": self.spectrum_sweeps,
            "spectrum_failures": self.spectrum_failures,
            "spectrum_candidates_total": self.spectrum_candidates_total,
            "last_spectrum": self.last_spectrum,
            "frames_received": self.frames_received,
            "bytes_received": self.bytes_received,
            "frames_forwarded": self.frames_forwarded,
            "rms": self.last_rms,
            "baseline_rms": self.last_baseline_rms,
            "threshold_rms": self.last_threshold_rms,
            "active_frames": self.last_active_frames,
            "audio_udp_host": self.udp_target[0],
            "audio_udp_port": self.udp_target[1],
            "demod_sample_rate_hz": int(
                self.worker.get("demod_rate_hz") or DEMOD_RATE
            ),
            "audio_sample_rate_hz": AUDIO_RATE,
            "demod_frame_bytes": DEMOD_FRAME_BYTES,
            "audio_frame_bytes": AUDIO_FRAME_BYTES,
            "audio_decimation": DECIMATION,
            "gain_db": float(self.worker.get("gain_db") or 49.6),
            "ppm": int(self.worker.get("ppm") or 0),
            "uptime_seconds": round(time.monotonic() - self.started_monotonic, 1),
            "last_lock": self.last_lock,
            "updated_epoch": time.time(),
        }
        if channel is not None:
            payload["current_channel"] = {
                "id": channel.get("id"),
                "name": channel.get("name"),
                "frequency_hz": int(channel["frequency_hz"]),
                "mode": channel.get("mode", "fm"),
                "priority": int(channel.get("priority") or 0),
            }
        payload.update(extra)
        atomic_json(self.status_path, payload)

    def open_channel(self, channel: dict[str, Any]) -> subprocess.Popen[bytes]:
        command = rtl_fm_command(self.serial, channel, self.worker)
        return subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            bufsize=0,
        )

    def scan_channel(self, channel: dict[str, Any]) -> None:
        spectrum_candidate = "spectrum_margin_db" in channel
        settle_seconds = float(
            self.worker.get(
                "spectrum_candidate_settle_seconds"
                if spectrum_candidate
                else "settle_seconds"
            )
            or (0.10 if spectrum_candidate else 0.18)
        )
        dwell_seconds = float(
            self.worker.get(
                "spectrum_candidate_dwell_seconds"
                if spectrum_candidate
                else "dwell_seconds"
            )
            or (2.5 if spectrum_candidate else 0.55)
        )
        hold_seconds = float(channel.get("hold_seconds") or 1.0)
        release_seconds = float(channel.get("resume_delay_seconds") or 1.25)
        configured_squelch = int(
            channel.get("squelch_rms")
            or self.worker.get("lock_squelch_rms")
            or 1200
        )
        release_squelch = int(
            channel.get("release_squelch_rms")
            or self.worker.get("release_squelch_rms")
            or max(0, configured_squelch - 75)
        )
        release_squelch = min(release_squelch, configured_squelch)
        lock_window_frames = max(
            2,
            int(self.worker.get("lock_window_frames") or 4),
        )
        lock_confirm_frames = max(
            1,
            min(
                lock_window_frames,
                int(self.worker.get("lock_confirm_frames") or 2),
            ),
        )
        prebuffer: Deque[bytes] = collections.deque(maxlen=20)
        recent: Deque[bool] = collections.deque(maxlen=lock_window_frames)
        baseline_values: list[int] = []
        locked = False
        last_active = 0.0
        release_below_since: float | None = None
        channel_started = time.monotonic()
        settle_until = channel_started + settle_seconds
        dwell_until = channel_started + settle_seconds + dwell_seconds
        threshold = configured_squelch
        self.last_threshold_rms = threshold
        self.last_active_frames = 0
        demod_buffer = bytearray()
        live_status_at = time.monotonic()

        self.channel_tunes += 1
        self.status("tuning", channel, squelch_rms=threshold)
        process = self.open_channel(channel)
        self.process = process
        assert process.stdout is not None

        try:
            pcm_deadline = time.monotonic() + float(
                self.worker.get("pcm_watchdog_seconds") or 3.0
            )
            heartbeat_at = time.monotonic() + 1.0
            while not self.stop_requested:
                now = time.monotonic()
                timeout = max(0.0, min(0.5, pcm_deadline - now))
                ready, _, _ = select.select([process.stdout], [], [], timeout)
                if not ready:
                    now = time.monotonic()
                    if now >= heartbeat_at:
                        self.status(
                            "locked" if locked else "tuning",
                            channel,
                            watchdog_waiting=True,
                            lock_confirm_frames=lock_confirm_frames,
                            lock_window_frames=lock_window_frames,
                        )
                        heartbeat_at = now + 1.0
                    if now >= pcm_deadline:
                        self.watchdog_timeouts += 1
                        self.child_restarts += 1
                        self.status(
                            "retrying",
                            channel,
                            error="rtl_fm PCM watchdog timeout",
                            watchdog_timeout=True,
                        )
                        self.stop_process()
                        time.sleep(0.35)
                        return
                    continue

                chunk = os.read(
                    process.stdout.fileno(),
                    DEMOD_FRAME_BYTES - len(demod_buffer),
                )
                if not chunk:
                    self.child_restarts += 1
                    self.status("retrying", channel, error="rtl_fm stdout closed")
                    return

                demod_buffer.extend(chunk)
                if len(demod_buffer) < DEMOD_FRAME_BYTES:
                    continue

                demod_data = bytes(demod_buffer[:DEMOD_FRAME_BYTES])
                del demod_buffer[:DEMOD_FRAME_BYTES]

                data = downsample_pcm16_3x(demod_data)
                if len(data) != AUDIO_FRAME_BYTES:
                    self.child_restarts += 1
                    self.status(
                        "retrying",
                        channel,
                        error=(
                            "unexpected decimated PCM frame size "
                            f"{len(data)} != {AUDIO_FRAME_BYTES}"
                        ),
                    )
                    return

                pcm_deadline = time.monotonic() + float(
                    self.worker.get("pcm_watchdog_seconds") or 3.0
                )
                heartbeat_at = time.monotonic() + 1.0
                self.frames_received += 1
                self.bytes_received += len(data)
                now = time.monotonic()
                value = rms_pcm16(data)
                self.last_rms = value
                prebuffer.append(data)

                if now < settle_until:
                    baseline_values.append(value)
                    if self.frames_received % 10 == 0:
                        self.status("settling", channel, rms=value)
                    continue

                if baseline_values:
                    ordered = sorted(baseline_values)
                    baseline = ordered[len(ordered) // 2]
                else:
                    baseline = 0
                threshold_rise = int(
                    self.worker.get("lock_threshold_above_baseline_rms") or 200
                )
                adaptive = baseline + threshold_rise
                threshold = (
                    configured_squelch
                    if spectrum_candidate
                    else max(configured_squelch, adaptive)
                )
                self.last_baseline_rms = baseline
                self.last_threshold_rms = threshold
                active = value >= threshold
                recent.append(active)
                active_frames = sum(recent)
                self.last_active_frames = active_frames
                confirmed = active_frames >= lock_confirm_frames

                if now >= live_status_at:
                    self.status(
                        "locked" if locked else "tuning",
                        channel,
                        rms=value,
                        baseline_rms=baseline,
                        threshold_rms=threshold,
                        active_frames=active_frames,
                        lock_confirm_frames=lock_confirm_frames,
                        lock_window_frames=lock_window_frames,
                        spectrum_candidate=spectrum_candidate,
                        adaptive_threshold_bypassed=spectrum_candidate,
                    )
                    live_status_at = now + 0.25

                if not locked and confirmed:
                    locked = True
                    self.lock_count += 1
                    last_active = now
                    release_below_since = None
                    self.last_lock = {
                        "name": channel.get("name"),
                        "frequency_hz": int(channel["frequency_hz"]),
                        "rms": value,
                        "threshold_rms": threshold,
                        "started_epoch": time.time(),
                    }
                    if not self.no_forward:
                        for frame in prebuffer:
                            self.udp.sendto(frame, self.udp_target)
                            self.frames_forwarded += 1
                    self.status(
                        "locked",
                        channel,
                        rms=value,
                        baseline_rms=baseline,
                        threshold_rms=threshold,
                        lock_confirm_frames=lock_confirm_frames,
                        lock_window_frames=lock_window_frames,
                    )

                if locked:
                    if confirmed:
                        last_active = now
                        release_below_since = None
                    elif value <= release_squelch:
                        if release_below_since is None:
                            release_below_since = now
                    if not self.no_forward:
                        self.udp.sendto(data, self.udp_target)
                        self.frames_forwarded += 1
                    release_elapsed = (
                        0.0
                        if release_below_since is None
                        else now - release_below_since
                    )
                    if release_elapsed >= max(hold_seconds, release_seconds):
                        self.status(
                            "releasing",
                            channel,
                            rms=value,
                            baseline_rms=baseline,
                            threshold_rms=threshold,
                        )
                        if len(self.worker.get("channels") or []) == 1:
                            locked = False
                            recent.clear()
                            prebuffer.clear()
                            last_active = 0.0
                            release_below_since = None
                            self.last_active_frames = 0
                            self.status(
                                "tuning",
                                channel,
                                rms=value,
                                baseline_rms=baseline,
                                threshold_rms=threshold,
                                active_frames=0,
                                lock_confirm_frames=lock_confirm_frames,
                                lock_window_frames=lock_window_frames,
                            )
                            continue
                        return
                elif now >= dwell_until:
                    self.status(
                        "scanning",
                        channel,
                        rms=value,
                        baseline_rms=baseline,
                        threshold_rms=threshold,
                    )
                    return
        finally:
            self.stop_process()
            time.sleep(0.12)

    def acquire_spectrum_candidates(self) -> list[dict[str, Any]]:
        channels = self.worker["channels"]
        frequencies = sorted({int(c["frequency_hz"]) for c in channels})
        segments = build_spectrum_segments(
            frequencies,
            int(self.worker.get("spectrum_segment_max_gap_hz") or 500000),
            int(self.worker.get("spectrum_edge_padding_hz") or 50000),
            int(self.worker.get("spectrum_minimum_span_hz") or 250000),
        )
        if not segments:
            raise ScannerError(f"{self.role} produced no spectrum segments")

        bin_hz = int(self.worker.get("spectrum_bin_hz") or 12500)
        gain = float(self.worker.get("gain_db") or 49.6)
        margin_required = float(self.worker.get("spectrum_margin_db") or 6.0)
        candidate_limit = max(1, int(self.worker.get("spectrum_candidate_limit") or 12))
        capture_seconds = max(
            1.0,
            float(self.worker.get("spectrum_capture_seconds") or 2.0),
        )
        timeout_seconds = max(
            capture_seconds + 5.0,
            float(
                self.worker.get("spectrum_segment_timeout_seconds")
                or 8.0
            ),
        )
        release_seconds = float(self.worker.get("receiver_release_seconds") or 1.25)
        by_frequency = {int(c["frequency_hz"]): c for c in channels}
        blocked = {int(v) for v in self.worker.get("blocked_frequencies_hz", [])}
        candidates = []
        metrics = []
        sweep_started = time.monotonic()

        for index, segment in enumerate(segments, 1):
            low_hz = int(segment["low_hz"])
            high_hz = int(segment["high_hz"])
            segment_frequencies = list(segment["frequencies_hz"])
            self.status(
                "spectrum_scanning",
                spectrum_segment_index=index,
                spectrum_segment_count=len(segments),
                spectrum_low_hz=low_hz,
                spectrum_high_hz=high_hz,
                spectrum_capture_seconds=capture_seconds,
            )
            segment_started = time.monotonic()
            with tempfile.TemporaryDirectory(prefix=f"pi-scanner-{self.role}-") as temporary:
                output = Path(temporary) / "rtl_power.csv"
                command = [
                    "rtl_power", "-d", self.serial,
                    "-f", f"{low_hz}:{high_hz}:{bin_hz}",
                    "-i", "1",
                    "-e", f"{capture_seconds:g}s",
                    "-g", str(gain), str(output),
                ]
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                deadline = time.monotonic() + timeout_seconds
                heartbeat = 0.0
                timed_out = False
                while process.poll() is None:
                    now = time.monotonic()
                    if self.stop_requested or now >= deadline:
                        timed_out = now >= deadline
                        try:
                            os.killpg(process.pid, signal.SIGINT)
                            process.wait(timeout=2.0)
                        except (ProcessLookupError, subprocess.TimeoutExpired):
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        break
                    if now >= heartbeat:
                        self.status(
                            "spectrum_scanning",
                            spectrum_segment_index=index,
                            spectrum_segment_count=len(segments),
                            spectrum_low_hz=low_hz,
                            spectrum_high_hz=high_hz,
                            spectrum_elapsed_seconds=round(now - sweep_started, 1),
                        )
                        heartbeat = now + 1.0
                    time.sleep(0.2)

                stderr_text = ""
                if process.stderr is not None:
                    stderr_text = process.stderr.read().decode("utf-8", errors="replace")[-1000:]
                if timed_out or process.returncode not in (0, None):
                    self.spectrum_failures += 1
                    metrics.append({
                        "index": index, "low_hz": low_hz, "high_hz": high_hz,
                        "channel_count": len(segment_frequencies), "ok": False,
                        "error": "timeout" if timed_out else stderr_text,
                        "duration_seconds": round(time.monotonic() - segment_started, 3),
                    })
                    time.sleep(release_seconds)
                    continue
                rows = parse_rtl_power_csv(output)

            if not rows:
                self.spectrum_failures += 1
                metrics.append({
                    "index": index, "low_hz": low_hz, "high_hz": high_hz,
                    "channel_count": len(segment_frequencies), "ok": False,
                    "error": "no parseable rows",
                    "duration_seconds": round(time.monotonic() - segment_started, 3),
                })
                time.sleep(release_seconds)
                continue

            found = 0
            for frequency_hz in segment_frequencies:
                if frequency_hz in blocked:
                    continue
                measurement = spectrum_power_at(rows, frequency_hz)
                if measurement is None:
                    continue
                power_db, noise_floor_db = measurement
                margin_db = power_db - noise_floor_db
                if margin_db < margin_required:
                    continue
                channel = dict(by_frequency[frequency_hz])
                channel["spectrum_power_db"] = round(power_db, 2)
                channel["spectrum_noise_floor_db"] = round(noise_floor_db, 2)
                channel["spectrum_margin_db"] = round(margin_db, 2)
                candidates.append(channel)
                found += 1

            metrics.append({
                "index": index, "low_hz": low_hz, "high_hz": high_hz,
                "channel_count": len(segment_frequencies), "ok": True,
                "candidate_count": found,
                "duration_seconds": round(time.monotonic() - segment_started, 3),
            })
            time.sleep(release_seconds)

        successful = sum(1 for item in metrics if item.get("ok"))
        if successful == 0:
            raise ScannerError(f"all {len(segments)} spectrum segments failed")

        candidates.sort(
            key=lambda item: (
                float(item.get("spectrum_margin_db") or -999.0),
                int(item.get("priority") or 0),
            ),
            reverse=True,
        )
        selected = candidates[:candidate_limit]
        self.spectrum_sweeps += 1
        self.spectrum_candidates_total += len(selected)
        self.last_spectrum = {
            "mode": "csv_segmented",
            "low_hz": min(frequencies),
            "high_hz": max(frequencies),
            "bin_hz": bin_hz,
            "duration_seconds": round(time.monotonic() - sweep_started, 3),
            "segment_count": len(segments),
            "successful_segment_count": successful,
            "failed_segment_count": len(segments) - successful,
            "evaluated_channel_count": len(frequencies),
            "candidate_count": len(candidates),
            "selected_candidate_count": len(selected),
            "capture_seconds": capture_seconds,
            "capture_mode": "explicit_duration",
            "segments": metrics,
            "completed_epoch": time.time(),
        }
        self.status(
            "spectrum_candidates",
            spectrum_segment_count=len(segments),
            spectrum_successful_segments=successful,
            spectrum_candidate_count=len(selected),
        )
        return selected

    def run_fast_spectrum(
        self,
        started: float,
        max_seconds: float | None,
    ) -> int:
        release_seconds = float(
            self.worker.get("receiver_release_seconds") or 1.25
        )
        while not self.stop_requested:
            if (
                max_seconds is not None
                and time.monotonic() - started >= max_seconds
            ):
                self.status("smoke_passed")
                return 0
            try:
                candidates = self.acquire_spectrum_candidates()
            except ScannerError as exc:
                self.status("spectrum_error", error=str(exc))
                time.sleep(1.25)
                continue

            if not candidates:
                self.scan_cycles += 1
                self.status("spectrum_scanning")
                time.sleep(0.25)
                continue

            for candidate in candidates:
                if self.stop_requested:
                    break
                before_locks = self.lock_count
                time.sleep(release_seconds)
                try:
                    self.scan_channel(candidate)
                except ScannerError as exc:
                    self.child_restarts += 1
                    self.status(
                        "candidate_error",
                        candidate,
                        error=str(exc),
                    )
                    time.sleep(0.75)
                    continue
                if self.lock_count > before_locks:
                    break

            self.scan_cycles += 1
            self.status("spectrum_scanning")

        self.status("stopped")
        return 0

    def run(self, max_seconds: float | None = None) -> int:
        started = time.monotonic()
        self.status("starting")
        search_mode = str(
            self.worker.get("search_mode") or "linear"
        ).strip().lower()
        if search_mode == "fast_spectrum":
            return self.run_fast_spectrum(started, max_seconds)

        while not self.stop_requested:
            for channel in self.worker["channels"]:
                if self.stop_requested:
                    break
                if max_seconds is not None and time.monotonic() - started >= max_seconds:
                    self.status("smoke_passed")
                    return 0
                try:
                    self.scan_channel(channel)
                except ScannerError as exc:
                    self.status("error", channel, error=str(exc))
                    time.sleep(0.75)
            self.scan_cycles += 1
            self.status("scanning")
        self.status("stopped")
        return 0


def self_test(role: str, config: Path, template: Path) -> int:
    worker, source = load_config(config, role, template)
    defaults = ROLE_DEFAULTS[role]
    channels = worker["channels"]
    if len(channels) != defaults["expected_channels"]:
        raise ScannerError(
            f"{role} expected {defaults['expected_channels']} channels; "
            f"found {len(channels)} in {source}"
        )
    command = rtl_fm_command(defaults["serial"], channels[0], worker)
    required = (
        "-s", "24000", "-g", "49.6",
        "-p", "0", "dc", "deemp",
    )
    missing = [token for token in required if token not in command]
    if missing:
        raise ScannerError(f"command missing required tokens: {missing}")
    forbidden = ("-r", "offset")
    present_forbidden = [token for token in forbidden if token in command]
    if present_forbidden:
        raise ScannerError(
            f"command contains obsolete tokens: {present_forbidden}"
        )
    with tempfile.TemporaryDirectory() as temporary:
        sample = Path(temporary) / "spectrum.csv"
        sample.write_text(
            "2026-07-24,00:00:00,146000000,146050000,12500,1,"
            "-50,-49,-40,-51,-50\n",
            encoding="utf-8",
        )
        rows = parse_rtl_power_csv(sample)
        measurement = spectrum_power_at(rows, 146025000)
        if measurement is None or measurement[0] != -40.0:
            raise ScannerError("spectrum parser self-test failed")
    demod_samples = array.array("h", range(-240, 240))
    if sys.byteorder != "little":
        demod_samples.byteswap()
    decimated = downsample_pcm16_3x(demod_samples.tobytes())
    if len(decimated) != AUDIO_FRAME_BYTES:
        raise ScannerError(
            "24 kHz to 8 kHz downsampling self-test failed"
        )
    synthetic = array.array("h", [0, 1000, -1000, 2000, -2000] * 64)
    if sys.byteorder != "little":
        synthetic.byteswap()
    if rms_pcm16(synthetic.tobytes()) <= 0:
        raise ScannerError("RMS self-test failed")
    print(f"PASS: continuous {role} scanner self-test")
    print("FINAL: PASS")
    return 0


def main(default_role: str | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=sorted(ROLE_DEFAULTS), default=default_role)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--template",
        type=Path,
        default=TEMPLATE_CONFIG,
        help="Fallback configuration template used when --config is absent.",
    )
    parser.add_argument("--status-path", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--smoke-seconds", type=float)
    parser.add_argument("--no-forward", action="store_true")
    args = parser.parse_args()

    if not args.role:
        parser.error("--role is required")
    role = args.role
    status_path = args.status_path or (
        DEFAULT_STATUS_DIR / ROLE_DEFAULTS[role]["status_name"]
    )
    if args.self_test:
        return self_test(role, args.config, args.template)

    if not args.config.exists() and args.template.exists():
        args.config = args.template
    scanner = ContinuousScanner(role, args.config, status_path, args.no_forward)
    signal.signal(signal.SIGTERM, scanner.request_stop)
    signal.signal(signal.SIGINT, scanner.request_stop)
    return scanner.run(args.smoke_seconds)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ScannerError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        print("FINAL: FAIL", file=sys.stderr)
        raise SystemExit(1)
