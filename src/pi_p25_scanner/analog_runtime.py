# PI-SCANNER analog service status and control helpers.

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .analog_worker import DEFAULT_CONFIG_PATH, load_analog_config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATUS_DIR = PROJECT_ROOT / "runtime" / "status"
UNIT_MAP = {
    "analog_2m": "pi-scanner-analog-2m.service",
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
        "active_state": active.stdout.strip() or properties.get("ActiveState", "unknown"),
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
        return {"state": "status_error", "status_path": str(path), "error": str(exc)}
    payload["status_path"] = str(path)
    return payload


def audio_arbiter_status() -> dict[str, Any]:
    try:
        with urllib.request.urlopen(AUDIO_STATUS_URL, timeout=1.0) as response:
            return json.loads(response.read(512 * 1024).decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc), "url": AUDIO_STATUS_URL}


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


def analog_service_action(role: str, action: str) -> dict[str, Any]:
    if role not in UNIT_MAP:
        raise AnalogRuntimeError(f"analog role is not controllable in this phase: {role}")
    if action not in ("start", "stop", "restart"):
        raise AnalogRuntimeError(f"unsupported analog action: {action}")
    unit = UNIT_MAP[role]
    result = _run_systemctl(action, unit)
    if result.returncode != 0:
        raise AnalogRuntimeError(
            f"systemctl {action} {unit} failed: "
            + (result.stderr.strip() or result.stdout.strip() or f"rc={result.returncode}")
        )
    time.sleep(0.35)
    payload = analog_status_payload()
    payload["action"] = action
    payload["role"] = role
    return payload
