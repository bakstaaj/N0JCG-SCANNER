# PI-SCANNER configurable analog RTL-SDR receiver worker.

from __future__ import annotations

import argparse
import array
import json
import math
import os
import select
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "runtime" / "settings" / "analog_receivers.json"
DEFAULT_TEMPLATE_PATH = PROJECT_ROOT / "config" / "analog_receivers.example.json"
DEFAULT_STATUS_DIR = PROJECT_ROOT / "runtime" / "status"


class AnalogWorkerError(RuntimeError):
    pass


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def ensure_analog_config(
    config_path: Path = DEFAULT_CONFIG_PATH,
    template_path: Path = DEFAULT_TEMPLATE_PATH,
) -> dict[str, Any]:
    config_path = Path(config_path)
    if config_path.exists():
        return {"created": False, "path": str(config_path)}
    if not template_path.exists():
        raise AnalogWorkerError(f"analog template missing: {template_path}")
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    validate_analog_config(payload)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"created": True, "path": str(config_path), "template": str(template_path)}


def validate_worker(role: str, item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise AnalogWorkerError(f"worker {role!r} must be an object")
    serial = str(item.get("rtl_serial") or "").strip()
    if len(serial) != 8 or not serial.isdigit():
        raise AnalogWorkerError(f"worker {role!r} has invalid rtl_serial {serial!r}")
    audio_rate = int(item.get("audio_rate_hz") or 8000)
    frame_bytes = int(item.get("frame_bytes") or 320)
    if audio_rate != 8000:
        raise AnalogWorkerError(f"worker {role!r} must use 8000 Hz browser audio")
    if frame_bytes != 320:
        raise AnalogWorkerError(f"worker {role!r} must use 320-byte PCM frames")
    channels = []
    for raw in item.get("channels", []):
        if not isinstance(raw, dict) or not bool(raw.get("enabled", True)):
            continue
        frequency_hz = int(raw.get("frequency_hz") or 0)
        if frequency_hz < 24_000_000 or frequency_hz > 1_766_000_000:
            raise AnalogWorkerError(
                f"worker {role!r} channel frequency outside RTL-SDR range: {frequency_hz}"
            )
        channels.append(
            {
                "frequency_hz": frequency_hz,
                "label": str(raw.get("label") or frequency_hz),
            }
        )
    if not channels:
        raise AnalogWorkerError(f"worker {role!r} has no enabled channels")
    return {
        "role": role,
        "enabled": bool(item.get("enabled", False)),
        "rtl_serial": serial,
        "modulation": str(item.get("modulation") or "fm"),
        "gain_db": float(item.get("gain_db", 40.2)),
        "ppm": int(item.get("ppm") or 0),
        "sample_rate_hz": int(item.get("sample_rate_hz") or 24000),
        "audio_rate_hz": audio_rate,
        "audio_udp_port": int(item.get("audio_udp_port") or 23458),
        "frame_bytes": frame_bytes,
        "dwell_seconds": max(0.5, float(item.get("dwell_seconds") or 2.0)),
        "hang_seconds": max(0.1, float(item.get("hang_seconds") or 0.9)),
        "squelch_rms": max(0, int(item.get("squelch_rms") or 1800)),
        "channels": channels,
    }


def validate_analog_config(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AnalogWorkerError("analog config must be an object")
    workers = payload.get("workers")
    if not isinstance(workers, dict):
        raise AnalogWorkerError("analog config must contain workers")
    normalized = {
        role: validate_worker(role, item)
        for role, item in workers.items()
    }
    if "analog_2m" not in normalized or "analog_70cm" not in normalized:
        raise AnalogWorkerError("analog_2m and analog_70cm workers are required")
    serials = [item["rtl_serial"] for item in normalized.values()]
    if len(serials) != len(set(serials)):
        raise AnalogWorkerError("analog workers cannot share an RTL serial")
    ports = [item["audio_udp_port"] for item in normalized.values()]
    if len(ports) != len(set(ports)):
        raise AnalogWorkerError("analog workers cannot share an audio UDP port")
    return {
        "schema_version": int(payload.get("schema_version") or 1),
        "audio_udp_host": str(payload.get("audio_udp_host") or "127.0.0.1"),
        "workers": normalized,
    }


def load_analog_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    ensure_analog_config(config_path=config_path)
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AnalogWorkerError(f"analog config JSON invalid: {exc}") from exc
    return validate_analog_config(payload)


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
    mean_square = sum(int(value) * int(value) for value in values) / len(values)
    return int(math.sqrt(mean_square))


class AnalogWorker:
    def __init__(
        self,
        role: str,
        config: dict[str, Any],
        status_path: Path,
        no_forward: bool = False,
        smoke_seconds: float = 0.0,
    ) -> None:
        workers = config["workers"]
        if role not in workers:
            raise AnalogWorkerError(f"unknown analog role: {role}")
        self.role = role
        self.config = workers[role]
        self.udp_host = config["audio_udp_host"]
        self.status_path = Path(status_path)
        self.no_forward = bool(no_forward)
        self.smoke_seconds = max(0.0, float(smoke_seconds))
        self.keep_running = True
        self.started_utc = time.time()
        self.frames_received = 0
        self.frames_forwarded = 0
        self.bytes_received = 0
        self.channels_visited = 0
        self.last_rms = 0
        self.peak_rms = 0
        self.current_channel: dict[str, Any] | None = None
        self.current_process: subprocess.Popen[bytes] | None = None
        self.stderr_lines: deque[str] = deque(maxlen=30)
        self.last_error = ""
        self.last_activity_utc: float | None = None
        self.smoke_deadline = (
            self.started_utc + self.smoke_seconds if self.smoke_seconds > 0 else None
        )
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def request_stop(self, *_args: Any) -> None:
        self.keep_running = False
        process = self.current_process
        if process is not None and process.poll() is None:
            process.terminate()

    def status_payload(self, state: str = "running") -> dict[str, Any]:
        process = self.current_process
        return {
            "ok": not bool(self.last_error),
            "role": self.role,
            "state": state,
            "worker_pid": os.getpid(),
            "rtl_process_pid": process.pid if process is not None and process.poll() is None else None,
            "rtl_serial": self.config["rtl_serial"],
            "current_channel": self.current_channel,
            "audio_udp_host": self.udp_host,
            "audio_udp_port": self.config["audio_udp_port"],
            "no_forward": self.no_forward,
            "smoke_seconds": self.smoke_seconds,
            "frames_received": self.frames_received,
            "frames_forwarded": self.frames_forwarded,
            "bytes_received": self.bytes_received,
            "channels_visited": self.channels_visited,
            "last_rms": self.last_rms,
            "peak_rms": self.peak_rms,
            "squelch_rms": self.config["squelch_rms"],
            "last_activity_utc": self.last_activity_utc,
            "last_error": self.last_error,
            "stderr_tail": list(self.stderr_lines),
            "started_utc": self.started_utc,
            "updated_utc": time.time(),
        }

    def write_status(self, state: str = "running") -> None:
        atomic_json_write(self.status_path, self.status_payload(state=state))

    def rtl_command(self, channel: dict[str, Any]) -> list[str]:
        return [
            "rtl_fm",
            "-d",
            self.config["rtl_serial"],
            "-f",
            str(channel["frequency_hz"]),
            "-M",
            self.config["modulation"],
            "-s",
            str(self.config["sample_rate_hz"]),
            "-r",
            str(self.config["audio_rate_hz"]),
            "-g",
            str(self.config["gain_db"]),
            "-p",
            str(self.config["ppm"]),
            "-l",
            "0",
        ]

    def stderr_reader(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stderr is not None
        while True:
            line = process.stderr.readline()
            if not line:
                return
            self.stderr_lines.append(line.decode("utf-8", errors="replace").rstrip())

    def run_channel(self, channel: dict[str, Any]) -> None:
        self.current_channel = dict(channel)
        self.channels_visited += 1
        command = self.rtl_command(channel)
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.current_process = process
        threading.Thread(target=self.stderr_reader, args=(process,), daemon=True).start()
        assert process.stdout is not None
        fd = process.stdout.fileno()
        buffer = bytearray()
        channel_started = time.time()
        last_active = 0.0
        self.write_status("tuning")

        while self.keep_running and process.poll() is None:
            now = time.time()
            if self.smoke_deadline is not None and now >= self.smoke_deadline:
                self.keep_running = False
                break
            ready, _, _ = select.select([fd], [], [], 0.25)
            if ready:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                self.bytes_received += len(chunk)
                buffer.extend(chunk)
                frame_bytes = self.config["frame_bytes"]
                while len(buffer) >= frame_bytes:
                    frame = bytes(buffer[:frame_bytes])
                    del buffer[:frame_bytes]
                    self.frames_received += 1
                    rms = pcm_rms(frame)
                    self.last_rms = rms
                    self.peak_rms = max(self.peak_rms, rms)
                    if rms >= self.config["squelch_rms"]:
                        last_active = time.time()
                        self.last_activity_utc = last_active
                        if not self.no_forward:
                            self.udp_socket.sendto(
                                frame,
                                (self.udp_host, self.config["audio_udp_port"]),
                            )
                            self.frames_forwarded += 1
            now = time.time()
            active_hold = last_active > 0 and now - last_active <= self.config["hang_seconds"]
            if (
                self.smoke_deadline is None
                and not active_hold
                and now - channel_started >= self.config["dwell_seconds"]
            ):
                break
            if self.frames_received % 20 == 0:
                self.write_status("active" if active_hold else "scanning")

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        rc = process.returncode
        self.current_process = None
        if rc not in (0, -signal.SIGTERM) and self.keep_running:
            self.last_error = f"rtl_fm exited rc={rc}"
            self.write_status("error")
            time.sleep(1.0)

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        self.write_status("starting")
        channels = list(self.config["channels"])
        index = 0
        try:
            while self.keep_running:
                channel = channels[index % len(channels)]
                index += 1
                self.run_channel(channel)
                if self.smoke_deadline is not None:
                    break
        finally:
            self.request_stop()
            self.udp_socket.close()

        if self.smoke_seconds > 0:
            if self.bytes_received <= 0 or self.frames_received <= 0:
                self.last_error = self.last_error or "hardware smoke received no PCM data"
                self.write_status("smoke_failed")
                return 1
            self.last_error = ""
            self.write_status("smoke_passed")
            return 0
        self.write_status("stopped")
        return 0


def self_test() -> int:
    payload = json.loads(DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8"))
    normalized = validate_analog_config(payload)
    tone = array.array("h", [0, 1000, -1000, 2000, -2000])
    if sys.byteorder != "little":
        tone.byteswap()
    if pcm_rms(tone.tobytes()) <= 0:
        print("FAIL: PCM RMS self-test")
        return 1
    if normalized["workers"]["analog_2m"]["rtl_serial"] != "00000440":
        print("FAIL: analog_2m serial self-test")
        return 1
    print("PASS: analog worker self-test")
    print("FINAL: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PI-SCANNER analog receiver worker")
    parser.add_argument("--role", default="analog_2m")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--status-path", default="")
    parser.add_argument("--smoke-seconds", type=float, default=0.0)
    parser.add_argument("--no-forward", action="store_true")
    parser.add_argument("--ensure-config", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.ensure_config:
        print(json.dumps(ensure_analog_config(Path(args.config)), indent=2))
        return 0

    config = load_analog_config(Path(args.config))
    status_path = (
        Path(args.status_path)
        if args.status_path
        else DEFAULT_STATUS_DIR / f"{args.role}.json"
    )
    worker = AnalogWorker(
        role=args.role,
        config=config,
        status_path=status_path,
        no_forward=args.no_forward,
        smoke_seconds=args.smoke_seconds,
    )
    return worker.run()


if __name__ == "__main__":
    raise SystemExit(main())
