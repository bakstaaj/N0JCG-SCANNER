#!/usr/bin/env python3
"""Isolated VHF analog scanner worker for PI-SCANNER.

This phase intentionally handles only the VHF receiver role (analog_2m) bound
to RTL-SDR serial 00000440. Active 8 kHz signed 16-bit mono PCM frames are sent
to UDP port 23458. P25 services and the UHF receiver are not modified.
"""

from __future__ import annotations

import argparse
import array
import copy
import json
import math
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "runtime" / "settings" / "analog_receivers.json"
DEFAULT_TEMPLATE_PATH = PROJECT_ROOT / "config" / "analog_receivers.example.json"
DEFAULT_STATUS_PATH = PROJECT_ROOT / "runtime" / "status" / "analog_2m.json"

ROLE = "analog_2m"
EXPECTED_SERIAL = "00000440"
EXPECTED_UDP_PORT = 23458
EXPECTED_AUDIO_RATE_HZ = 8000
EXPECTED_FRAME_BYTES = 320
RTL_MIN_HZ = 24_000_000
RTL_MAX_HZ = 1_766_000_000


class VhfWorkerError(RuntimeError):
    """Raised when VHF configuration or runtime validation fails."""


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def pcm_rms(frame: bytes) -> int:
    usable = len(frame) - (len(frame) % 2)
    if usable <= 0:
        return 0
    samples = array.array("h")
    samples.frombytes(frame[:usable])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0
    mean_square = sum(int(value) * int(value) for value in samples) / len(samples)
    return int(math.sqrt(mean_square))


def normalize_mode(value: Any) -> str:
    mode = str(value or "fm").strip().lower().replace("-", "").replace("_", "")
    aliases = {
        "fm": "fm",
        "widefm": "fm",
        "nfm": "nfm",
        "narrowfm": "nfm",
        "fmn": "nfm",
        "am": "am",
    }
    if mode not in aliases:
        raise VhfWorkerError(f"unsupported analog mode: {value!r}")
    return aliases[mode]


def rtl_fm_mode(mode: str) -> str:
    return "am" if normalize_mode(mode) == "am" else "fm"


def load_vhf_config(
    config_path: Path = DEFAULT_CONFIG_PATH,
    template_path: Path = DEFAULT_TEMPLATE_PATH,
) -> dict[str, Any]:
    config_path = Path(config_path)
    template_path = Path(template_path)
    source_path = config_path if config_path.exists() else template_path
    if not source_path.exists():
        raise VhfWorkerError(
            f"analog configuration missing: {config_path} and {template_path}"
        )
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VhfWorkerError(f"invalid analog configuration JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise VhfWorkerError("analog configuration must be a JSON object")

    workers = payload.get("workers")
    if not isinstance(workers, dict):
        raise VhfWorkerError("analog configuration must contain workers")
    raw_worker = workers.get(ROLE)
    if not isinstance(raw_worker, dict):
        raise VhfWorkerError(f"analog configuration is missing {ROLE}")

    serial = str(raw_worker.get("rtl_serial") or "").strip()
    if serial != EXPECTED_SERIAL:
        raise VhfWorkerError(
            f"{ROLE} must remain bound to RTL serial {EXPECTED_SERIAL}; "
            f"received {serial!r}"
        )

    audio_rate_hz = int(raw_worker.get("audio_rate_hz") or EXPECTED_AUDIO_RATE_HZ)
    frame_bytes = int(raw_worker.get("frame_bytes") or EXPECTED_FRAME_BYTES)
    udp_port = int(raw_worker.get("audio_udp_port") or EXPECTED_UDP_PORT)
    if audio_rate_hz != EXPECTED_AUDIO_RATE_HZ:
        raise VhfWorkerError(f"{ROLE} must use {EXPECTED_AUDIO_RATE_HZ} Hz audio")
    if frame_bytes != EXPECTED_FRAME_BYTES:
        raise VhfWorkerError(f"{ROLE} must use {EXPECTED_FRAME_BYTES}-byte frames")
    if udp_port != EXPECTED_UDP_PORT:
        raise VhfWorkerError(f"{ROLE} must use UDP port {EXPECTED_UDP_PORT}")

    defaults = {
        "gain_db": float(raw_worker.get("gain_db", 40.2)),
        "squelch_rms": max(0, int(raw_worker.get("squelch_rms") or 1800)),
        "hold_seconds": max(0.1, float(raw_worker.get("hang_seconds") or 0.9)),
        "resume_delay_seconds": max(
            0.0, float(raw_worker.get("resume_delay_seconds") or 1.2)
        ),
    }
    channels: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_worker.get("channels") or []):
        if not isinstance(raw, dict):
            raise VhfWorkerError(f"{ROLE} channel {index + 1} must be an object")
        if not bool(raw.get("enabled", True)):
            continue
        frequency_hz = int(raw.get("frequency_hz") or 0)
        if not RTL_MIN_HZ <= frequency_hz <= RTL_MAX_HZ:
            raise VhfWorkerError(
                f"{ROLE} channel {index + 1} is outside RTL-SDR range"
            )
        name = str(raw.get("name") or f"{frequency_hz / 1_000_000:.6f} MHz").strip()
        channels.append(
            {
                "id": str(raw.get("id") or f"{ROLE}-{index + 1}"),
                "name": name[:120],
                "frequency_hz": frequency_hz,
                "mode": normalize_mode(raw.get("mode")),
                "priority": max(0, min(100, int(raw.get("priority") or 0))),
                "gain_db": float(raw.get("gain_db", defaults["gain_db"])),
                "squelch_rms": max(
                    0, int(raw.get("squelch_rms", defaults["squelch_rms"]))
                ),
                "hold_seconds": max(
                    0.1,
                    min(
                        30.0,
                        float(raw.get("hold_seconds", defaults["hold_seconds"])),
                    ),
                ),
                "resume_delay_seconds": max(
                    0.0,
                    min(
                        30.0,
                        float(
                            raw.get(
                                "resume_delay_seconds",
                                defaults["resume_delay_seconds"],
                            )
                        ),
                    ),
                ),
                "ctcss_hz": raw.get("ctcss_hz"),
                "dcs_code": str(raw.get("dcs_code") or ""),
            }
        )
    if not channels:
        raise VhfWorkerError(f"{ROLE} has no enabled channels")

    channels.sort(key=lambda item: (-item["priority"], item["frequency_hz"]))
    return {
        "source_path": str(source_path),
        "enabled": bool(raw_worker.get("enabled", False)),
        "rtl_serial": serial,
        "audio_udp_host": str(payload.get("audio_udp_host") or "127.0.0.1"),
        "audio_udp_port": udp_port,
        "sample_rate_hz": int(raw_worker.get("sample_rate_hz") or 24000),
        "audio_rate_hz": audio_rate_hz,
        "frame_bytes": frame_bytes,
        "ppm": int(raw_worker.get("ppm") or 0),
        "dwell_seconds": max(
            0.25, min(20.0, float(raw_worker.get("dwell_seconds") or 1.0))
        ),
        "settle_seconds": max(
            0.0, min(2.0, float(raw_worker.get("settle_seconds") or 0.20))
        ),
        "channels": channels,
    }


class VhfScannerWorker:
    def __init__(
        self,
        config: dict[str, Any],
        status_path: Path,
        *,
        no_forward: bool = False,
        smoke_seconds: float = 0.0,
    ) -> None:
        self.config = config
        self.status_path = Path(status_path)
        self.no_forward = bool(no_forward)
        self.smoke_seconds = max(0.0, float(smoke_seconds))
        self.started_utc = time.time()
        self.smoke_deadline = (
            self.started_utc + self.smoke_seconds
            if self.smoke_seconds > 0
            else None
        )
        self.keep_running = True
        self.current_process: subprocess.Popen[bytes] | None = None
        self.current_channel: dict[str, Any] | None = None
        self.last_active_channel: dict[str, Any] | None = None
        self.last_error = ""
        self.last_rms = 0
        self.peak_rms = 0
        self.bytes_received = 0
        self.frames_received = 0
        self.frames_forwarded = 0
        self.channels_visited = 0
        self.scan_cycle_count = 0
        self.activity_events = 0
        self.last_activity_utc: float | None = None
        self.stderr_lines: deque[str] = deque(maxlen=30)
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def request_stop(self, *_args: Any) -> None:
        self.keep_running = False
        process = self.current_process
        if process is not None and process.poll() is None:
            process.terminate()

    def rtl_command(self, channel: dict[str, Any]) -> list[str]:
        return [
            "rtl_fm",
            "-d",
            self.config["rtl_serial"],
            "-f",
            str(channel["frequency_hz"]),
            "-M",
            rtl_fm_mode(channel["mode"]),
            "-s",
            str(self.config["sample_rate_hz"]),
            "-r",
            str(self.config["audio_rate_hz"]),
            "-g",
            str(channel["gain_db"]),
            "-p",
            str(self.config["ppm"]),
            "-l",
            "0",
        ]

    def status_payload(self, state: str) -> dict[str, Any]:
        process = self.current_process
        return {
            "ok": not bool(self.last_error),
            "phase": "isolated-vhf-worker",
            "role": ROLE,
            "state": state,
            "worker_pid": os.getpid(),
            "rtl_process_pid": (
                process.pid
                if process is not None and process.poll() is None
                else None
            ),
            "rtl_serial": self.config["rtl_serial"],
            "config_source": self.config["source_path"],
            "channel_count": len(self.config["channels"]),
            "current_channel": self.current_channel,
            "last_active_channel": self.last_active_channel,
            "audio_udp_host": self.config["audio_udp_host"],
            "audio_udp_port": self.config["audio_udp_port"],
            "browser_audio_url": "http://DEVICE-IP:8073/audio.wav",
            "no_forward": self.no_forward,
            "smoke_seconds": self.smoke_seconds,
            "bytes_received": self.bytes_received,
            "frames_received": self.frames_received,
            "frames_forwarded": self.frames_forwarded,
            "channels_visited": self.channels_visited,
            "scan_cycle_count": self.scan_cycle_count,
            "activity_events": self.activity_events,
            "last_rms": self.last_rms,
            "peak_rms": self.peak_rms,
            "last_activity_utc": self.last_activity_utc,
            "last_error": self.last_error,
            "stderr_tail": list(self.stderr_lines),
            "started_utc": self.started_utc,
            "updated_utc": time.time(),
        }

    def write_status(self, state: str) -> None:
        atomic_json_write(self.status_path, self.status_payload(state))

    def _read_stderr(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stderr is not None
        while True:
            line = process.stderr.readline()
            if not line:
                return
            self.stderr_lines.append(
                line.decode("utf-8", errors="replace").rstrip()
            )

    def _terminate_process(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def run_channel(self, channel: dict[str, Any]) -> None:
        self.current_channel = copy.deepcopy(channel)
        self.channels_visited += 1
        try:
            process = subprocess.Popen(
                self.rtl_command(channel),
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError as exc:
            raise VhfWorkerError("rtl_fm is not installed") from exc

        self.current_process = process
        threading.Thread(
            target=self._read_stderr,
            args=(process,),
            daemon=True,
        ).start()
        assert process.stdout is not None
        fd = process.stdout.fileno()
        buffer = bytearray()
        channel_started = time.time()
        last_active = 0.0
        activity_seen = False
        last_status_write = 0.0
        self.write_status("tuning")

        while self.keep_running and process.poll() is None:
            now = time.time()
            if self.smoke_deadline is not None and now >= self.smoke_deadline:
                self.keep_running = False
                break

            ready, _, _ = select.select([fd], [], [], 0.20)
            if ready:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                self.bytes_received += len(chunk)
                buffer.extend(chunk)
                while len(buffer) >= self.config["frame_bytes"]:
                    frame = bytes(buffer[: self.config["frame_bytes"]])
                    del buffer[: self.config["frame_bytes"]]
                    self.frames_received += 1
                    rms = pcm_rms(frame)
                    self.last_rms = rms
                    self.peak_rms = max(self.peak_rms, rms)

                    settled = (
                        time.time() - channel_started
                        >= self.config["settle_seconds"]
                    )
                    if settled and rms >= channel["squelch_rms"]:
                        if not activity_seen:
                            self.activity_events += 1
                        activity_seen = True
                        last_active = time.time()
                        self.last_activity_utc = last_active
                        self.last_active_channel = copy.deepcopy(channel)
                        if not self.no_forward:
                            self.udp_socket.sendto(
                                frame,
                                (
                                    self.config["audio_udp_host"],
                                    self.config["audio_udp_port"],
                                ),
                            )
                            self.frames_forwarded += 1

            now = time.time()
            state = "scanning"
            should_advance = False
            if activity_seen:
                silence_age = now - last_active
                if silence_age <= channel["hold_seconds"]:
                    state = "active"
                elif silence_age <= (
                    channel["hold_seconds"]
                    + channel["resume_delay_seconds"]
                ):
                    state = "reply_delay"
                else:
                    should_advance = True
            elif now - channel_started >= self.config["dwell_seconds"]:
                should_advance = True

            if now - last_status_write >= 0.5:
                self.write_status(state)
                last_status_write = now
            if self.smoke_deadline is None and should_advance:
                break

        self._terminate_process(process)
        return_code = process.returncode
        self.current_process = None
        if (
            return_code not in (0, -signal.SIGTERM)
            and self.keep_running
            and self.bytes_received == 0
        ):
            self.last_error = f"rtl_fm exited rc={return_code}"
            self.write_status("error")
            time.sleep(1.0)

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)

        if not self.config["enabled"]:
            self.write_status("disabled")
            return 0

        self.write_status("starting")
        try:
            while self.keep_running:
                for channel in self.config["channels"]:
                    if not self.keep_running:
                        break
                    self.run_channel(channel)
                    if self.smoke_deadline is not None:
                        break
                self.scan_cycle_count += 1
                if self.smoke_deadline is not None:
                    break
        except Exception as exc:
            self.last_error = str(exc)
            self.write_status("error")
            return 1
        finally:
            self.request_stop()
            self.udp_socket.close()

        if self.smoke_seconds > 0:
            if self.bytes_received <= 0 or self.frames_received <= 0:
                self.last_error = self.last_error or "hardware smoke received no PCM"
                self.write_status("smoke_failed")
                return 1
            self.last_error = ""
            self.write_status("smoke_passed")
            return 0

        self.write_status("stopped")
        return 0


def self_test() -> int:
    config = load_vhf_config(DEFAULT_CONFIG_PATH, DEFAULT_TEMPLATE_PATH)
    synthetic = array.array("h", [0, 1000, -1000, 2000, -2000])
    if sys.byteorder != "little":
        synthetic.byteswap()
    worker = VhfScannerWorker(config, Path(os.devnull), no_forward=True)
    try:
        command = worker.rtl_command(config["channels"][0])
        checks = [
            config["rtl_serial"] == EXPECTED_SERIAL,
            config["audio_udp_port"] == EXPECTED_UDP_PORT,
            len(config["channels"]) == 31,
            pcm_rms(synthetic.tobytes()) > 0,
            "-d" in command and EXPECTED_SERIAL in command,
            "-f" in command,
            rtl_fm_mode("nfm") == "fm",
            rtl_fm_mode("am") == "am",
        ]
    finally:
        worker.udp_socket.close()
    if not all(checks):
        print(json.dumps(config, indent=2))
        print("FINAL: FAIL")
        return 1
    print("PASS: isolated VHF worker self-test")
    print("FINAL: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PI-SCANNER isolated VHF analog scanner worker"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE_PATH))
    parser.add_argument("--status-path", default=str(DEFAULT_STATUS_PATH))
    parser.add_argument("--smoke-seconds", type=float, default=0.0)
    parser.add_argument("--no-forward", action="store_true")
    parser.add_argument("--print-config", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    config = load_vhf_config(Path(args.config), Path(args.template))
    if args.print_config:
        print(json.dumps(config, indent=2))
        return 0

    worker = VhfScannerWorker(
        config,
        Path(args.status_path),
        no_forward=args.no_forward,
        smoke_seconds=args.smoke_seconds,
    )
    return worker.run()


if __name__ == "__main__":
    raise SystemExit(main())
