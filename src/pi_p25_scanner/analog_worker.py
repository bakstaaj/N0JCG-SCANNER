# PI-SCANNER configurable analog RTL-SDR receiver worker.

from __future__ import annotations

import argparse
import array
import copy
import json
import math
import os
import re
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

from .analog_activity import (
    activity_log_path,
    append_completed_event,
    complete_activity_event,
    new_activity_event,
)  # PHASE6_ANALOG_ACTIVITY_HISTORY_V0_6E
from .analog_recordings import (
    WavRecordingSession,
    enforce_retention,
)  # PHASE7_ANALOG_RECORDING_PLAYBACK_V0_6F
from .analog_ctcss import CtcssDetector  # PHASE8_CTCSS_TONE_GATE_V0_6G
from .analog_dcs import DcsDetector, parse_dcs_code  # PHASE9_DCS_TONE_GATE_V0_6H

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "runtime" / "settings" / "analog_receivers.json"
DEFAULT_TEMPLATE_PATH = PROJECT_ROOT / "config" / "analog_receivers.example.json"
DEFAULT_STATUS_DIR = PROJECT_ROOT / "runtime" / "status"
EXPECTED_ANALOG_SERIALS = {
    "analog_2m": "00000440",
    "analog_70cm": "00000144",
}
EXPECTED_AUDIO_PORTS = {
    "analog_2m": 23458,
    "analog_70cm": 23459,
}
ALLOWED_MODES = {"nfm", "fm", "am"}


class AnalogWorkerError(RuntimeError):
    pass


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def channel_id(role: str, index: int, raw_name: Any) -> str:
    supplied = str(raw_name or "").strip()
    if supplied:
        clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", supplied).strip("-._")
        if clean:
            return clean[:80]
    return f"{role}-{index + 1}"


def normalize_mode(value: Any, fallback: str = "nfm") -> str:
    mode = str(value or fallback).strip().lower()
    aliases = {
        "narrowfm": "nfm",
        "narrow_fm": "nfm",
        "fm-n": "nfm",
        "fm": "fm",
        "widefm": "fm",
        "am": "am",
    }
    mode = aliases.get(mode, mode)
    if mode not in ALLOWED_MODES:
        raise AnalogWorkerError(f"unsupported analog mode: {value!r}")
    return mode


def rtl_fm_mode(mode: str) -> str:
    normalized = normalize_mode(mode)
    return "am" if normalized == "am" else "fm"


def optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def normalize_channel(
    role: str,
    index: int,
    raw: Any,
    worker_defaults: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AnalogWorkerError(f"{role} channel {index + 1} must be an object")
    frequency_hz = int(raw.get("frequency_hz") or 0)
    if frequency_hz < 24_000_000 or frequency_hz > 1_766_000_000:
        raise AnalogWorkerError(
            f"{role} channel {index + 1} frequency outside RTL-SDR range: {frequency_hz}"
        )
    name = str(raw.get("name") or raw.get("label") or frequency_hz).strip()
    if not name:
        name = str(frequency_hz)
    dcs_code = str(raw.get("dcs_code") or "").strip().upper()
    if dcs_code:
        try:
            dcs_code = parse_dcs_code(dcs_code)["display"]
        except Exception as exc:
            raise AnalogWorkerError(
                f"{role} channel {index + 1} has invalid DCS code: {dcs_code!r}"
            ) from exc
    dcs_gate = bool(raw.get("dcs_gate", False))
    if dcs_gate and not dcs_code:
        raise AnalogWorkerError(
            f"{role} channel {index + 1} enables DCS Gate without a DCS code"
        )
    ctcss_hz = optional_float(raw.get("ctcss_hz"))
    if ctcss_hz is not None and not 50.0 <= ctcss_hz <= 300.0:
        raise AnalogWorkerError(
            f"{role} channel {index + 1} CTCSS must be 50.0 through 300.0 Hz"
        )
    tone_gate = bool(raw.get("tone_gate", False))
    if tone_gate and ctcss_hz is None:
        raise AnalogWorkerError(
            f"{role} channel {index + 1} enables Tone Gate without a CTCSS frequency"
        )
    if tone_gate and dcs_gate:
        raise AnalogWorkerError(
            f"{role} channel {index + 1} cannot enable CTCSS Gate and DCS Gate together"
        )
    return {
        "id": channel_id(role, index, raw.get("id") or name),
        "enabled": bool(raw.get("enabled", True)),
        "name": name[:120],
        "frequency_hz": frequency_hz,
        "mode": normalize_mode(
            raw.get("mode"),
            fallback=str(worker_defaults.get("modulation") or "nfm"),
        ),
        "priority": max(0, min(100, int(raw.get("priority") or 0))),
        "gain_db": float(raw.get("gain_db", worker_defaults["gain_db"])),
        "squelch_rms": max(
            0,
            int(raw.get("squelch_rms", worker_defaults["squelch_rms"])),
        ),
        "hold_seconds": max(
            0.1,
            min(
                30.0,
                float(raw.get("hold_seconds", worker_defaults["hang_seconds"])),
            ),
        ),
        "resume_delay_seconds": max(
            0.0,
            min(
                30.0,
                float(
                    raw.get(
                        "resume_delay_seconds",
                        worker_defaults["resume_delay_seconds"],
                    )
                ),
            ),
        ),
        "ctcss_hz": ctcss_hz,
        "tone_gate": tone_gate,
        "dcs_code": dcs_code,
        "dcs_gate": dcs_gate,
        "recording_enabled": bool(raw.get("recording_enabled", False)),
    }


def validate_worker(role: str, item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise AnalogWorkerError(f"worker {role!r} must be an object")
    serial = str(item.get("rtl_serial") or "").strip()
    expected_serial = EXPECTED_ANALOG_SERIALS.get(role)
    if expected_serial and serial != expected_serial:
        raise AnalogWorkerError(
            f"worker {role!r} must remain bound to RTL serial {expected_serial}; "
            f"received {serial!r}"
        )
    if len(serial) != 8 or not serial.isdigit():
        raise AnalogWorkerError(f"worker {role!r} has invalid rtl_serial {serial!r}")

    audio_rate = int(item.get("audio_rate_hz") or 8000)
    frame_bytes = int(item.get("frame_bytes") or 320)
    if audio_rate != 8000:
        raise AnalogWorkerError(f"worker {role!r} must use 8000 Hz browser audio")
    if frame_bytes != 320:
        raise AnalogWorkerError(f"worker {role!r} must use 320-byte PCM frames")

    audio_port = int(
        item.get("audio_udp_port")
        or EXPECTED_AUDIO_PORTS.get(role, 23458)
    )
    expected_port = EXPECTED_AUDIO_PORTS.get(role)
    if expected_port and audio_port != expected_port:
        raise AnalogWorkerError(
            f"worker {role!r} must use audio UDP port {expected_port}"
        )

    defaults = {
        "gain_db": float(item.get("gain_db", 40.2)),
        "squelch_rms": max(0, int(item.get("squelch_rms") or 1800)),
        "hang_seconds": max(0.1, float(item.get("hang_seconds") or 0.9)),
        "resume_delay_seconds": max(
            0.0,
            float(item.get("resume_delay_seconds") or 1.2),
        ),
        "modulation": normalize_mode(item.get("modulation"), "nfm"),
    }
    channels = [
        normalize_channel(role, index, raw, defaults)
        for index, raw in enumerate(item.get("channels", []))
    ]
    if not channels:
        raise AnalogWorkerError(f"worker {role!r} has no channels")
    if not any(channel["enabled"] for channel in channels):
        raise AnalogWorkerError(f"worker {role!r} has no enabled channels")

    channel_ids = [channel["id"] for channel in channels]
    if len(channel_ids) != len(set(channel_ids)):
        raise AnalogWorkerError(f"worker {role!r} contains duplicate channel IDs")

    return {
        "role": role,
        "enabled": bool(item.get("enabled", False)),
        "rtl_serial": serial,
        "modulation": defaults["modulation"],
        "gain_db": defaults["gain_db"],
        "ppm": int(item.get("ppm") or 0),
        "sample_rate_hz": int(item.get("sample_rate_hz") or 24000),
        "audio_rate_hz": audio_rate,
        "audio_udp_port": audio_port,
        "frame_bytes": frame_bytes,
        "dwell_seconds": max(
            0.15,
            min(20.0, float(item.get("dwell_seconds") or 1.0)),
        ),
        "hang_seconds": defaults["hang_seconds"],
        "resume_delay_seconds": defaults["resume_delay_seconds"],
        "squelch_rms": defaults["squelch_rms"],
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
    for required in EXPECTED_ANALOG_SERIALS:
        if required not in normalized:
            raise AnalogWorkerError(f"required analog worker missing: {required}")
    serials = [item["rtl_serial"] for item in normalized.values()]
    if len(serials) != len(set(serials)):
        raise AnalogWorkerError("analog workers cannot share an RTL serial")
    ports = [item["audio_udp_port"] for item in normalized.values()]
    if len(ports) != len(set(ports)):
        raise AnalogWorkerError("analog workers cannot share an audio UDP port")
    return {
        "schema_version": 4,
        "audio_udp_host": str(
            payload.get("audio_udp_host") or "127.0.0.1"
        ),
        "workers": normalized,
    }


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
    normalized = validate_analog_config(payload)
    atomic_json_write(config_path, normalized)
    return {
        "created": True,
        "path": str(config_path),
        "template": str(template_path),
    }


def load_raw_analog_config(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, Any]:
    ensure_analog_config(config_path=config_path)
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AnalogWorkerError(f"analog config JSON invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnalogWorkerError("analog config must be an object")
    return payload


def load_analog_config(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, Any]:
    return validate_analog_config(load_raw_analog_config(config_path))


def write_analog_config(
    payload: dict[str, Any],
    config_path: Path = DEFAULT_CONFIG_PATH,
    backup: bool = True,
) -> dict[str, Any]:
    normalized = validate_analog_config(payload)
    path = Path(config_path)
    backup_path: Path | None = None
    if backup and path.exists():
        backup_dir = path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup_path = backup_dir / f"analog_receivers_{stamp}.json"
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    atomic_json_write(path, normalized)
    return {
        "ok": True,
        "config_path": str(path),
        "backup_path": str(backup_path) if backup_path else None,
        "config": normalized,
    }


def migrate_analog_config_file(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, Any]:
    raw = load_raw_analog_config(config_path)
    result = write_analog_config(raw, config_path=config_path, backup=True)
    result["migrated"] = True
    return result


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
        self.scan_cycle_count = 0
        self.activity_events = 0
        self.last_rms = 0
        self.peak_rms = 0
        self.current_channel: dict[str, Any] | None = None
        self.last_active_channel: dict[str, Any] | None = None
        self.current_process: subprocess.Popen[bytes] | None = None
        self.stderr_lines: deque[str] = deque(maxlen=30)
        self.last_error = ""
        self.last_activity_utc: float | None = None
        self.current_activity: dict[str, Any] | None = None
        self.activity_history_path = str(activity_log_path(role))
        self.current_recording: WavRecordingSession | None = None
        self.last_recording: dict[str, Any] | None = None
        self.ctcss_detector: CtcssDetector | None = None
        self.ctcss_snapshot: dict[str, Any] = {}
        self.ctcss_rejected_frames = 0
        self.ctcss_gate_open_frames = 0
        self.last_detected_ctcss_hz: float | None = None
        self.dcs_detector: DcsDetector | None = None
        self.dcs_snapshot: dict[str, Any] = {}
        self.dcs_rejected_frames = 0
        self.dcs_gate_open_frames = 0
        self.last_detected_dcs_code: str | None = None
        self.last_detected_dcs_polarity: str | None = None
        self.smoke_deadline = (
            self.started_utc + self.smoke_seconds
            if self.smoke_seconds > 0
            else None
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
            "rtl_process_pid": (
                process.pid
                if process is not None and process.poll() is None
                else None
            ),
            "rtl_serial": self.config["rtl_serial"],
            "current_channel": self.current_channel,
            "last_active_channel": self.last_active_channel,
            "audio_udp_host": self.udp_host,
            "audio_udp_port": self.config["audio_udp_port"],
            "no_forward": self.no_forward,
            "smoke_seconds": self.smoke_seconds,
            "frames_received": self.frames_received,
            "frames_forwarded": self.frames_forwarded,
            "bytes_received": self.bytes_received,
            "channels_visited": self.channels_visited,
            "scan_cycle_count": self.scan_cycle_count,
            "activity_events": self.activity_events,
            "last_rms": self.last_rms,
            "peak_rms": self.peak_rms,
            "last_activity_utc": self.last_activity_utc,
            "current_activity": self.current_activity,
            "activity_history_path": self.activity_history_path,
            "current_recording": (
                str(self.current_recording.path)
                if self.current_recording is not None
                else None
            ),
            "last_recording": self.last_recording,
            "ctcss_gate_required": bool(
                (self.current_channel or {}).get("tone_gate", False)
            ),
            "configured_ctcss_hz": (
                (self.current_channel or {}).get("ctcss_hz")
            ),
            "detected_ctcss_hz": self.last_detected_ctcss_hz,
            "ctcss_locked": bool(self.ctcss_snapshot.get("locked", False)),
            "ctcss_confidence": self.ctcss_snapshot.get("confidence", 0.0),
            "ctcss_detector": self.ctcss_snapshot,
            "ctcss_rejected_frames": self.ctcss_rejected_frames,
            "ctcss_gate_open_frames": self.ctcss_gate_open_frames,
            "dcs_gate_required": bool(
                (self.current_channel or {}).get("dcs_gate", False)
            ),
            "configured_dcs_code": (
                (self.current_channel or {}).get("dcs_code") or None
            ),
            "detected_dcs_code": self.last_detected_dcs_code,
            "detected_dcs_polarity": self.last_detected_dcs_polarity,
            "dcs_locked": bool(self.dcs_snapshot.get("locked", False)),
            "dcs_confidence": self.dcs_snapshot.get("confidence", 0.0),
            "dcs_detector": self.dcs_snapshot,
            "dcs_rejected_frames": self.dcs_rejected_frames,
            "dcs_gate_open_frames": self.dcs_gate_open_frames,
            "last_error": self.last_error,
            "stderr_tail": list(self.stderr_lines),
            "started_utc": self.started_utc,
            "updated_utc": time.time(),
        }

    def write_status(self, state: str = "running") -> None:
        atomic_json_write(self.status_path, self.status_payload(state=state))

    def enabled_channels(self) -> list[dict[str, Any]]:
        indexed = [
            (index, copy.deepcopy(channel))
            for index, channel in enumerate(self.config["channels"])
            if channel["enabled"]
        ]
        indexed.sort(key=lambda item: (-item[1]["priority"], item[0]))
        return [channel for _index, channel in indexed]

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

    def stderr_reader(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stderr is not None
        while True:
            line = process.stderr.readline()
            if not line:
                return
            self.stderr_lines.append(
                line.decode("utf-8", errors="replace").rstrip()
            )

    # PHASE6_ANALOG_ACTIVITY_HISTORY_V0_6E
    # PHASE7_ANALOG_RECORDING_PLAYBACK_V0_6F
    # PHASE8_CTCSS_TONE_GATE_V0_6G
    def begin_activity(
        self,
        channel: dict[str, Any],
        rms: int,
        opening_frames: list[bytes],
    ) -> None:
        if self.smoke_seconds > 0 or self.current_activity is not None:
            return
        event = new_activity_event(
            self.role,
            self.config["rtl_serial"],
            channel,
        )
        event["peak_rms"] = int(rms)
        event["active_frames"] = max(1, len(opening_frames))
        event["ctcss_gate_required"] = bool(channel.get("tone_gate", False))
        event["configured_ctcss_hz"] = channel.get("ctcss_hz")
        event["detected_ctcss_hz"] = self.last_detected_ctcss_hz
        event["ctcss_confidence"] = self.ctcss_snapshot.get("confidence", 0.0)
        event["dcs_gate_required"] = bool(channel.get("dcs_gate", False))
        event["configured_dcs_code"] = channel.get("dcs_code") or None
        event["detected_dcs_code"] = self.last_detected_dcs_code
        event["detected_dcs_polarity"] = self.last_detected_dcs_polarity
        event["dcs_confidence"] = self.dcs_snapshot.get("confidence", 0.0)
        self.current_activity = event
        if bool(channel.get("recording_enabled", False)):
            try:
                self.current_recording = WavRecordingSession(
                    self.role,
                    event["event_id"],
                    rate_hz=self.config["audio_rate_hz"],
                )
                for frame in opening_frames:
                    self.current_recording.write(frame)
            except Exception as exc:
                self.current_recording = None
                event["recording_error"] = str(exc)

    def update_activity(self, rms: int) -> None:
        if self.current_activity is None:
            return
        self.current_activity["peak_rms"] = max(
            int(self.current_activity.get("peak_rms") or 0),
            int(rms),
        )
        self.current_activity["active_frames"] = (
            int(self.current_activity.get("active_frames") or 0) + 1
        )
        if self.last_detected_ctcss_hz is not None:
            self.current_activity["detected_ctcss_hz"] = (
                self.last_detected_ctcss_hz
            )
            self.current_activity["ctcss_confidence"] = max(
                float(self.current_activity.get("ctcss_confidence") or 0.0),
                float(self.ctcss_snapshot.get("confidence") or 0.0),
            )
        if self.last_detected_dcs_code is not None:
            self.current_activity["detected_dcs_code"] = (
                self.last_detected_dcs_code
            )
            self.current_activity["detected_dcs_polarity"] = (
                self.last_detected_dcs_polarity
            )
            self.current_activity["dcs_confidence"] = max(
                float(self.current_activity.get("dcs_confidence") or 0.0),
                float(self.dcs_snapshot.get("confidence") or 0.0),
            )

    def end_activity(self, reason: str) -> None:
        if self.current_activity is None:
            return
        completed = complete_activity_event(
            self.current_activity,
            end_reason=reason,
        )
        if self.current_recording is not None:
            try:
                recording = self.current_recording.close()
                completed.update(recording)
                self.last_recording = recording
                enforce_retention()
            except Exception as exc:
                completed["recording_error"] = str(exc)
            finally:
                self.current_recording = None
        append_completed_event(completed)
        self.current_activity = None

    def run_channel(self, channel: dict[str, Any]) -> None:
        self.current_channel = copy.deepcopy(channel)
        self.channels_visited += 1
        process = subprocess.Popen(
            self.rtl_command(channel),
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.current_process = process
        threading.Thread(
            target=self.stderr_reader,
            args=(process,),
            daemon=True,
        ).start()
        assert process.stdout is not None
        fd = process.stdout.fileno()
        buffer = bytearray()
        channel_started = time.time()
        last_active = 0.0
        activity_seen = False
        last_state_write = 0.0
        configured_ctcss = channel.get("ctcss_hz")
        tone_gate_required = bool(channel.get("tone_gate", False))
        configured_dcs = str(channel.get("dcs_code") or "").strip()
        dcs_gate_required = bool(channel.get("dcs_gate", False))
        self.ctcss_detector = (
            CtcssDetector(
                float(configured_ctcss),
                sample_rate_hz=self.config["audio_rate_hz"],
            )
            if configured_ctcss is not None
            else None
        )
        self.ctcss_snapshot = (
            self.ctcss_detector.snapshot()
            if self.ctcss_detector is not None
            else {}
        )
        self.last_detected_ctcss_hz = None
        self.dcs_detector = (
            DcsDetector(
                configured_dcs,
                sample_rate_hz=self.config["audio_rate_hz"],
            )
            if configured_dcs
            else None
        )
        self.dcs_snapshot = (
            self.dcs_detector.snapshot()
            if self.dcs_detector is not None
            else {}
        )
        self.last_detected_dcs_code = None
        self.last_detected_dcs_polarity = None
        prebuffer_frames: deque[bytes] = deque(maxlen=32)
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
                frame_bytes = self.config["frame_bytes"]
                while len(buffer) >= frame_bytes:
                    frame = bytes(buffer[:frame_bytes])
                    del buffer[:frame_bytes]
                    self.frames_received += 1
                    rms = pcm_rms(frame)
                    prebuffer_frames.append(frame)
                    if self.current_recording is not None:
                        self.current_recording.write(frame)
                    self.last_rms = rms
                    self.peak_rms = max(self.peak_rms, rms)

                    if self.ctcss_detector is not None:
                        self.ctcss_snapshot = self.ctcss_detector.feed(frame)
                        if self.ctcss_snapshot.get("locked"):
                            self.last_detected_ctcss_hz = float(
                                self.ctcss_snapshot["detected_hz"]
                            )

                    if self.dcs_detector is not None:
                        self.dcs_snapshot = self.dcs_detector.feed(frame)
                        if self.dcs_snapshot.get("locked"):
                            self.last_detected_dcs_code = str(
                                self.dcs_snapshot["detected_code"]
                            )
                            self.last_detected_dcs_polarity = str(
                                self.dcs_snapshot["detected_polarity"]
                            )

                    carrier_open = rms >= channel["squelch_rms"]
                    tone_locked = bool(
                        self.ctcss_snapshot.get("locked", False)
                    )
                    tone_recent = False
                    last_tone_match = self.ctcss_snapshot.get("last_match_utc")
                    if last_tone_match is not None:
                        tone_recent = (
                            time.time() - float(last_tone_match) <= 0.60
                        )

                    dcs_locked = bool(
                        self.dcs_snapshot.get("locked", False)
                    )
                    dcs_recent = False
                    last_dcs_match = self.dcs_snapshot.get("last_match_utc")
                    if last_dcs_match is not None:
                        dcs_recent = (
                            time.time() - float(last_dcs_match) <= 0.75
                        )

                    ctcss_qualified = (
                        not tone_gate_required
                        or tone_locked
                        or (activity_seen and tone_recent)
                    )
                    dcs_qualified = (
                        not dcs_gate_required
                        or dcs_locked
                        or (activity_seen and dcs_recent)
                    )
                    gate_open = (
                        carrier_open
                        and ctcss_qualified
                        and dcs_qualified
                    )

                    if carrier_open and tone_gate_required and not ctcss_qualified:
                        self.ctcss_rejected_frames += 1
                    if carrier_open and dcs_gate_required and not dcs_qualified:
                        self.dcs_rejected_frames += 1

                    if gate_open:
                        signaling_gate_required = (
                            tone_gate_required or dcs_gate_required
                        )
                        opening_frames = (
                            list(prebuffer_frames)
                            if signaling_gate_required and not activity_seen
                            else [frame]
                        )
                        if not activity_seen:
                            self.activity_events += 1
                            self.begin_activity(
                                channel,
                                rms,
                                opening_frames,
                            )
                        else:
                            self.update_activity(rms)
                        activity_seen = True
                        if tone_gate_required:
                            self.ctcss_gate_open_frames += 1
                        if dcs_gate_required:
                            self.dcs_gate_open_frames += 1
                        last_active = time.time()
                        self.last_activity_utc = last_active
                        self.last_active_channel = copy.deepcopy(channel)
                        if not self.no_forward:
                            for output_frame in opening_frames:
                                self.udp_socket.sendto(
                                    output_frame,
                                    (
                                        self.udp_host,
                                        self.config["audio_udp_port"],
                                    ),
                                )
                                self.frames_forwarded += 1

            now = time.time()
            state = "scanning"
            should_advance = False
            if (
                not activity_seen
                and (tone_gate_required or dcs_gate_required)
                and self.last_rms >= channel["squelch_rms"]
            ):
                state = "signaling_search"
            if activity_seen:
                age = now - last_active
                if age <= channel["hold_seconds"]:
                    state = "active"
                elif age <= (
                    channel["hold_seconds"]
                    + channel["resume_delay_seconds"]
                ):
                    state = "reply_delay"
                else:
                    should_advance = True
            elif now - channel_started >= self.config["dwell_seconds"]:
                should_advance = True

            if now - last_state_write >= 0.5:
                self.write_status(state)
                last_state_write = now
            if self.smoke_deadline is None and should_advance:
                if activity_seen:
                    self.end_activity("squelch_closed")
                break

        if activity_seen:
            self.end_activity(
                "worker_stopped" if not self.keep_running else "channel_ended"
            )

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        rc = process.returncode
        self.current_process = None
        self.ctcss_detector = None
        self.dcs_detector = None
        if rc not in (0, -signal.SIGTERM) and self.keep_running:
            self.last_error = f"rtl_fm exited rc={rc}"
            self.write_status("error")
            time.sleep(1.0)

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        self.write_status("starting")
        try:
            while self.keep_running:
                channels = self.enabled_channels()
                for channel in channels:
                    if not self.keep_running:
                        break
                    self.run_channel(channel)
                    if self.smoke_deadline is not None:
                        break
                self.scan_cycle_count += 1
                if self.smoke_deadline is not None:
                    break
        finally:
            self.end_activity("worker_stopped")
            self.request_stop()
            self.udp_socket.close()

        if self.smoke_seconds > 0:
            if self.bytes_received <= 0 or self.frames_received <= 0:
                self.last_error = (
                    self.last_error
                    or "hardware smoke received no PCM data"
                )
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
    checks = [
        pcm_rms(tone.tobytes()) > 0,
        normalized["schema_version"] == 4,
        normalized["workers"]["analog_2m"]["rtl_serial"] == "00000440",
        normalized["workers"]["analog_70cm"]["rtl_serial"] == "00000144",
        normalized["workers"]["analog_2m"]["channels"][0]["mode"] == "nfm",
        rtl_fm_mode("nfm") == "fm",
        rtl_fm_mode("am") == "am",
    ]
    if not all(checks):
        print(json.dumps(normalized, indent=2))
        print("FINAL: FAIL")
        return 1
    bad = copy.deepcopy(payload)
    bad["workers"]["analog_2m"]["rtl_serial"] = "00000001"
    try:
        validate_analog_config(bad)
    except AnalogWorkerError:
        pass
    else:
        print("FAIL: protected analog serial was accepted")
        return 1
    print("PASS: analog worker/configuration self-test")
    print("FINAL: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PI-SCANNER analog receiver worker"
    )
    parser.add_argument("--role", default="analog_2m")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--status-path", default="")
    parser.add_argument("--smoke-seconds", type=float, default=0.0)
    parser.add_argument("--no-forward", action="store_true")
    parser.add_argument("--ensure-config", action="store_true")
    parser.add_argument("--migrate-config", action="store_true")
    parser.add_argument("--print-config", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if args.self_test:
        return self_test()
    if args.ensure_config:
        print(json.dumps(ensure_analog_config(config_path), indent=2))
        return 0
    if args.migrate_config:
        print(
            json.dumps(
                migrate_analog_config_file(config_path),
                indent=2,
            )
        )
        return 0
    if args.print_config:
        print(json.dumps(load_analog_config(config_path), indent=2))
        return 0

    config = load_analog_config(config_path)
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
