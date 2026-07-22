# PI-SCANNER analog service status, configuration, and control helpers.

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .analog_worker import (
    AnalogWorkerError,
    DEFAULT_CONFIG_PATH,
    load_analog_config,
    write_analog_config,
)
from .analog_activity import (
    activity_payload,
    clear_activity_history,
)  # PHASE6_ANALOG_ACTIVITY_HISTORY_V0_6E
from .analog_recordings import (
    AnalogRecordingError,
    clear_recordings,
    delete_recording,
    recordings_payload,
    resolve_recording_file,
)  # PHASE7_ANALOG_RECORDING_PLAYBACK_V0_6F

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATUS_DIR = PROJECT_ROOT / "runtime" / "status"
UNIT_MAP = {
    "analog_2m": "pi-scanner-analog-2m.service",
    "analog_70cm": "pi-scanner-analog-70cm.service",
}
AUDIO_STATUS_URL = "http://127.0.0.1:8072/api/audio/status"


class AnalogRuntimeError(RuntimeError):
    pass


def _run_systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sudo", "-n", "systemctl", *args],
        text=True,
        capture_output=True,
        timeout=12,
        check=False,
    )


def unit_state(unit: str) -> dict[str, Any]:
    active = _run_systemctl("is-active", unit)
    enabled = _run_systemctl("is-enabled", unit)
    show = _run_systemctl(
        "show",
        unit,
        "--property=MainPID,ActiveState,SubState,Result",
        "--no-pager",
    )
    properties: dict[str, str] = {}
    for line in show.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            properties[key] = value
    return {
        "unit": unit,
        "active": active.stdout.strip() == "active",
        "active_state": (
            active.stdout.strip()
            or properties.get("ActiveState", "unknown")
        ),
        "enabled": enabled.stdout.strip() == "enabled",
        "enabled_state": enabled.stdout.strip() or "unknown",
        "main_pid": int(properties.get("MainPID") or 0) or None,
        "sub_state": properties.get("SubState", ""),
        "result": properties.get("Result", ""),
    }


def read_worker_status(role: str) -> dict[str, Any]:
    path = STATUS_DIR / f"{role}.json"
    if not path.exists():
        return {"state": "not_started", "status_path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "state": "status_error",
            "status_path": str(path),
            "error": str(exc),
        }
    payload["status_path"] = str(path)
    return payload


def audio_arbiter_status() -> dict[str, Any]:
    try:
        with urllib.request.urlopen(
            AUDIO_STATUS_URL,
            timeout=1.0,
        ) as response:
            return json.loads(
                response.read(512 * 1024).decode("utf-8")
            )
    except (
        OSError,
        urllib.error.URLError,
        json.JSONDecodeError,
    ) as exc:
        return {
            "ok": False,
            "error": str(exc),
            "url": AUDIO_STATUS_URL,
        }


# PHASE6_ANALOG_ACTIVITY_HISTORY_V0_6E
def analog_activity_payload(limit: int = 100) -> dict[str, Any]:
    return activity_payload(limit=limit)


def clear_analog_activity(request: dict[str, Any]) -> dict[str, Any]:
    role = str(request.get("role") or "").strip() or None
    if role not in (None, "analog_2m", "analog_70cm"):
        raise AnalogRuntimeError(f"unsupported activity role: {role}")
    result = clear_activity_history(role=role)
    result["activity"] = activity_payload(limit=100)
    return result


# PHASE7_ANALOG_RECORDING_PLAYBACK_V0_6F
def analog_recordings_payload() -> dict[str, Any]:
    return recordings_payload(limit=500)


def resolve_analog_recording(request: dict[str, Any]) -> Path:
    role = str(request.get("role") or "").strip()
    filename = str(request.get("filename") or "").strip()
    return resolve_recording_file(role, filename)


def clear_analog_recordings(request: dict[str, Any]) -> dict[str, Any]:
    role = str(request.get("role") or "").strip() or None
    if role not in (None, "analog_2m", "analog_70cm"):
        raise AnalogRuntimeError(f"unsupported recording role: {role}")
    result = clear_recordings(role=role)
    result["recordings"] = recordings_payload(limit=500)
    return result


def delete_analog_recording(request: dict[str, Any]) -> dict[str, Any]:
    role = str(request.get("role") or "").strip()
    filename = str(request.get("filename") or "").strip()
    result = delete_recording(role, filename)
    result["recordings"] = recordings_payload(limit=500)
    return result


def analog_config_payload() -> dict[str, Any]:
    config = load_analog_config(DEFAULT_CONFIG_PATH)
    return {
        "ok": True,
        "schema_version": config["schema_version"],
        "config_path": str(DEFAULT_CONFIG_PATH),
        "config": config,
        "running_roles": [
            role
            for role, unit in UNIT_MAP.items()
            if unit_state(unit)["active"]
        ],
    }


def analog_status_payload() -> dict[str, Any]:
    config = load_analog_config(DEFAULT_CONFIG_PATH)
    workers = []
    for role, worker_config in config["workers"].items():
        unit = UNIT_MAP.get(role)
        service = unit_state(unit) if unit else {
            "unit": None,
            "active": False,
            "active_state": "not_installed",
            "enabled": False,
            "enabled_state": "not_installed",
            "main_pid": None,
            "sub_state": "",
            "result": "",
        }
        workers.append(
            {
                "role": role,
                "config": worker_config,
                "service": service,
                "runtime": read_worker_status(role),
                "controllable": bool(unit),
            }
        )
    return {
        "ok": True,
        "updated_utc": time.time(),
        "config_path": str(DEFAULT_CONFIG_PATH),
        "workers": workers,
        "audio_arbiter": audio_arbiter_status(),
    }


def analog_service_action(
    role: str,
    action: str,
) -> dict[str, Any]:
    if role not in UNIT_MAP:
        raise AnalogRuntimeError(
            f"analog role is not controllable: {role}"
        )
    if action not in ("start", "stop", "restart"):
        raise AnalogRuntimeError(
            f"unsupported analog action: {action}"
        )
    unit = UNIT_MAP[role]
    result = _run_systemctl(action, unit)
    if result.returncode != 0:
        raise AnalogRuntimeError(
            f"systemctl {action} {unit} failed: "
            + (
                result.stderr.strip()
                or result.stdout.strip()
                or f"rc={result.returncode}"
            )
        )
    time.sleep(0.35)
    payload = analog_status_payload()
    payload["action"] = action
    payload["role"] = role
    return payload


def save_analog_config(request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise AnalogWorkerError(
            "analog config save request must be an object"
        )
    payload = request.get("config", request)
    if not isinstance(payload, dict):
        raise AnalogWorkerError(
            "analog config save payload must be an object"
        )
    restart_running = bool(request.get("restart_running", True))
    running_before = [
        role
        for role, unit in UNIT_MAP.items()
        if unit_state(unit)["active"]
    ]
    result = write_analog_config(
        payload,
        config_path=DEFAULT_CONFIG_PATH,
        backup=True,
    )
    restarted_roles: list[str] = []
    if restart_running:
        for role in running_before:
            unit = UNIT_MAP[role]
            restarted = _run_systemctl("restart", unit)
            if restarted.returncode != 0:
                raise AnalogRuntimeError(
                    f"saved config but failed to restart {unit}: "
                    + (
                        restarted.stderr.strip()
                        or restarted.stdout.strip()
                        or f"rc={restarted.returncode}"
                    )
                )
            restarted_roles.append(role)
    time.sleep(0.35)
    return {
        "ok": True,
        **result,
        "restart_running": restart_running,
        "running_roles_before": running_before,
        "restarted_roles": restarted_roles,
        "status": analog_status_payload(),
    }
