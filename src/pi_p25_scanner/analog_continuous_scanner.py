#!/usr/bin/env python3
"""Continuous serial-bound analog scanner for PI-SCANNER."""

from __future__ import annotations

import argparse
import array
import collections
import json
import math
import os
import select
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Deque

ROLE_DEFAULTS = {
    "analog_2m": {
        "serial": "00000440",
        "udp_port": 23458,
        "status_name": "analog_2m.json",
        "expected_channels": 31,
    },
    "analog_70cm": {
        "serial": "00000144",
        "udp_port": 23459,
        "status_name": "analog_70cm.json",
        "expected_channels": 7,
    },
}

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "runtime/settings/analog_receivers.json"
TEMPLATE_CONFIG = ROOT / "config/analog_receivers.example.json"
DEFAULT_STATUS_DIR = ROOT / "runtime/status"

RF_INPUT_RATE = 240000
AUDIO_RATE = 8000
FRAME_MS = 20
FRAME_SAMPLES = AUDIO_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2
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
        "-s", str(int(worker.get("sample_rate_hz") or RF_INPUT_RATE)),
        "-r", str(int(worker.get("audio_rate_hz") or AUDIO_RATE)),
        "-g", str(gain),
        "-l", "0",
        "-p", str(ppm),
        "-E", "offset",
        "-E", "dc",
    ]
    if mode == "fm":
        command += ["-E", "deemp"]
    return command


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
        self.frames_received = 0
        self.bytes_received = 0
        self.frames_forwarded = 0
        self.last_lock: dict[str, Any] | None = None
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
            "frames_received": self.frames_received,
            "bytes_received": self.bytes_received,
            "frames_forwarded": self.frames_forwarded,
            "audio_udp_host": self.udp_target[0],
            "audio_udp_port": self.udp_target[1],
            "rf_input_sample_rate_hz": int(
                self.worker.get("sample_rate_hz") or RF_INPUT_RATE
            ),
            "audio_sample_rate_hz": int(
                self.worker.get("audio_rate_hz") or AUDIO_RATE
            ),
            "frame_bytes": FRAME_BYTES,
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
        settle_seconds = float(self.worker.get("settle_seconds") or 0.18)
        dwell_seconds = float(self.worker.get("dwell_seconds") or 0.55)
        hold_seconds = float(channel.get("hold_seconds") or 1.0)
        release_seconds = float(channel.get("resume_delay_seconds") or 1.25)
        configured_squelch = int(channel.get("squelch_rms") or 1800)
        prebuffer: Deque[bytes] = collections.deque(maxlen=20)
        recent: Deque[bool] = collections.deque(maxlen=5)
        baseline_values: list[int] = []
        locked = False
        last_active = 0.0
        channel_started = time.monotonic()
        settle_until = channel_started + settle_seconds
        dwell_until = channel_started + settle_seconds + dwell_seconds
        threshold = configured_squelch

        self.channel_tunes += 1
        self.status("tuning", channel, squelch_rms=threshold)
        self.process = self.open_channel(channel)
        assert self.process.stdout is not None

        try:
            pcm_deadline = time.monotonic() + float(
                self.worker.get("pcm_watchdog_seconds") or 3.0
            )
            heartbeat_at = time.monotonic() + 1.0
            while not self.stop_requested:
                now = time.monotonic()
                timeout = max(0.0, min(0.5, pcm_deadline - now))
                ready, _, _ = select.select([self.process.stdout], [], [], timeout)
                if not ready:
                    now = time.monotonic()
                    if now >= heartbeat_at:
                        self.status(
                            "locked" if locked else "tuning",
                            channel,
                            watchdog_waiting=True,
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

                data = os.read(self.process.stdout.fileno(), FRAME_BYTES)
                if not data:
                    self.child_restarts += 1
                    self.status("retrying", channel, error="rtl_fm stdout closed")
                    return

                pcm_deadline = time.monotonic() + float(
                    self.worker.get("pcm_watchdog_seconds") or 3.0
                )
                heartbeat_at = time.monotonic() + 1.0
                self.frames_received += 1
                self.bytes_received += len(data)
                now = time.monotonic()
                value = rms_pcm16(data)
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
                adaptive = int(baseline * 1.45 + 175)
                threshold = max(configured_squelch, adaptive)
                active = value >= threshold
                recent.append(active)
                confirmed = sum(recent) >= 3

                if not locked and confirmed:
                    locked = True
                    self.lock_count += 1
                    last_active = now
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
                    )

                if locked:
                    if active:
                        last_active = now
                    if not self.no_forward:
                        self.udp.sendto(data, self.udp_target)
                        self.frames_forwarded += 1
                    if now - last_active >= max(hold_seconds, release_seconds):
                        self.status(
                            "releasing",
                            channel,
                            rms=value,
                            baseline_rms=baseline,
                            threshold_rms=threshold,
                        )
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

    def run(self, max_seconds: float | None = None) -> int:
        started = time.monotonic()
        self.status("starting")
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
        "-s", "240000", "-r", "8000", "-g", "49.6",
        "-p", "0", "offset", "dc", "deemp",
    )
    missing = [token for token in required if token not in command]
    if missing:
        raise ScannerError(f"command missing required tokens: {missing}")
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
