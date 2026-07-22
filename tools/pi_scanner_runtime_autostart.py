#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = (
    PROJECT_ROOT / "runtime" / "settings" / "startup_policy.json"
)
DEFAULT_STATUS = (
    PROJECT_ROOT / "runtime" / "status" / "startup_orchestrator.json"
)
DEFAULT_BACKEND = "http://127.0.0.1:8091"
DEFAULT_AUDIO = "http://127.0.0.1:8072"

POLICY_DEFAULTS = {
    "schema_version": 1,
    "enabled": True,
    "p25_autostart": True,
    "analog_2m_autostart": True,
    "analog_70cm_autostart": True,
    "backend_wait_seconds": 75,
    "audio_wait_seconds": 45,
    "validation_seconds": 35,
    "request_timeout_seconds": 10
}


class AutostartError(RuntimeError):
    pass


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def normalize_policy(raw: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(POLICY_DEFAULTS)
    if isinstance(raw, dict):
        merged.update(raw)
    return {
        "schema_version": 1,
        "enabled": bool(merged.get("enabled", True)),
        "p25_autostart": bool(merged.get("p25_autostart", True)),
        "analog_2m_autostart": bool(
            merged.get("analog_2m_autostart", True)
        ),
        "analog_70cm_autostart": bool(
            merged.get("analog_70cm_autostart", True)
        ),
        "backend_wait_seconds": max(
            10, min(300, int(merged.get("backend_wait_seconds", 75)))
        ),
        "audio_wait_seconds": max(
            10, min(300, int(merged.get("audio_wait_seconds", 45)))
        ),
        "validation_seconds": max(
            5, min(180, int(merged.get("validation_seconds", 35)))
        ),
        "request_timeout_seconds": max(
            2, min(30, int(merged.get("request_timeout_seconds", 10)))
        ),
    }


def load_policy(path: Path) -> dict[str, Any]:
    if not path.exists():
        policy = normalize_policy(None)
        atomic_json(path, policy)
        return policy
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutostartError(f"cannot read startup policy {path}: {exc}") from exc
    return normalize_policy(raw)


def request_json(
    url: str,
    *,
    method: str = "GET",
    timeout: int = 10,
) -> dict[str, Any]:
    data = b"{}" if method == "POST" else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(
                response.read(1024 * 1024).decode("utf-8")
            )
    except (
        OSError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
    ) as exc:
        raise AutostartError(f"{method} {url} failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise AutostartError(f"{method} {url} returned non-object JSON")
    return payload


def wait_api(url: str, seconds: int, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            payload = request_json(url, timeout=min(timeout, 5))
            if payload.get("ok", True):
                return payload
            last_error = str(payload.get("error") or payload)
        except AutostartError as exc:
            last_error = str(exc)
        time.sleep(0.75)
    raise AutostartError(
        f"API not ready within {seconds}s: {url}: {last_error}"
    )


def worker_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("role") or ""): item
        for item in payload.get("workers") or []
        if isinstance(item, dict)
    }


def apply_policy(
    policy: dict[str, Any],
    *,
    backend: str,
    audio: str,
    status_path: Path,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "state": "starting",
        "started_utc": time.time(),
        "completed_utc": None,
        "policy": policy,
        "backend": backend,
        "audio": audio,
        "actions": [],
        "validation": {},
        "errors": [],
    }
    atomic_json(status_path, result)

    if not policy["enabled"]:
        result.update(
            {"ok": True, "state": "disabled", "completed_utc": time.time()}
        )
        atomic_json(status_path, result)
        return result

    timeout = policy["request_timeout_seconds"]

    try:
        wait_api(
            f"{backend}/api/status",
            policy["backend_wait_seconds"],
            timeout,
        )
        wait_api(
            f"{audio}/api/audio/status",
            policy["audio_wait_seconds"],
            timeout,
        )

        actions = [
            ("p25", policy["p25_autostart"], f"{backend}/api/scanner/start"),
            (
                "analog_2m",
                policy["analog_2m_autostart"],
                f"{backend}/api/analog/2m/start",
            ),
            (
                "analog_70cm",
                policy["analog_70cm_autostart"],
                f"{backend}/api/analog/70cm/start",
            ),
        ]

        for name, enabled, url in actions:
            if not enabled:
                result["actions"].append(
                    {
                        "name": name,
                        "enabled": False,
                        "action": "skipped",
                        "ok": True,
                    }
                )
                continue
            response = request_json(url, method="POST", timeout=timeout)
            result["actions"].append(
                {
                    "name": name,
                    "enabled": True,
                    "action": "start",
                    "ok": bool(response.get("ok", True)),
                }
            )

        deadline = time.monotonic() + policy["validation_seconds"]
        validation: dict[str, Any] = {}

        while time.monotonic() < deadline:
            scanner = request_json(f"{backend}/api/status", timeout=timeout)
            analog = request_json(
                f"{backend}/api/analog/status", timeout=timeout
            )
            audio_status = request_json(
                f"{audio}/api/audio/status", timeout=timeout
            )
            workers = worker_map(analog)
            checks = {
                "p25": (
                    not policy["p25_autostart"]
                    or bool(
                        (scanner.get("decoder_process") or {}).get("running")
                    )
                ),
                "analog_2m": (
                    not policy["analog_2m_autostart"]
                    or bool(
                        (workers.get("analog_2m", {}).get("service") or {})
                        .get("active")
                    )
                ),
                "analog_70cm": (
                    not policy["analog_70cm_autostart"]
                    or bool(
                        (
                            workers.get("analog_70cm", {})
                            .get("service")
                            or {}
                        ).get("active")
                    )
                ),
                "audio": bool(audio_status.get("ok", False)),
            }
            validation = {
                "checks": checks,
                "scanner_state": scanner.get("scanner_state"),
                "decoder_running": (
                    scanner.get("decoder_process") or {}
                ).get("running"),
                "analog_states": {
                    role: {
                        "service_active": bool(
                            (item.get("service") or {}).get("active")
                        ),
                        "runtime_state": (
                            item.get("runtime") or {}
                        ).get("state"),
                    }
                    for role, item in workers.items()
                },
                "audio_active_source": audio_status.get("active_source"),
            }
            if all(checks.values()):
                break
            time.sleep(0.75)

        result["validation"] = validation
        failed = [
            name
            for name, passed in (validation.get("checks") or {}).items()
            if not passed
        ]
        if failed:
            raise AutostartError(
                "startup validation failed: " + ", ".join(failed)
            )

        result.update(
            {"ok": True, "state": "ready", "completed_utc": time.time()}
        )
    except Exception as exc:
        result["errors"].append(str(exc))
        result.update(
            {"ok": False, "state": "error", "completed_utc": time.time()}
        )
        atomic_json(status_path, result)
        raise

    atomic_json(status_path, result)
    return result


def self_test() -> int:
    policy = normalize_policy(
        {
            "backend_wait_seconds": 1,
            "audio_wait_seconds": 9999,
            "validation_seconds": 0,
            "request_timeout_seconds": 100,
        }
    )
    checks = [
        policy["backend_wait_seconds"] == 10,
        policy["audio_wait_seconds"] == 300,
        policy["validation_seconds"] == 5,
        policy["request_timeout_seconds"] == 30,
        policy["p25_autostart"] is True,
        policy["analog_2m_autostart"] is True,
        policy["analog_70cm_autostart"] is True,
    ]
    if not all(checks):
        print(json.dumps({"policy": policy, "checks": checks}, indent=2))
        print("FINAL: FAIL")
        return 1
    print(json.dumps(policy, indent=2))
    print("PASS: runtime autostart self-test")
    print("FINAL: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    parser.add_argument("--audio", default=DEFAULT_AUDIO)
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    policy = load_policy(args.policy)

    if not args.apply:
        print(json.dumps({"policy": policy}, indent=2))
        return 0

    try:
        result = apply_policy(
            policy,
            backend=args.backend.rstrip("/"),
            audio=args.audio.rstrip("/"),
            status_path=args.status,
        )
    except Exception as exc:
        print(f"FAIL: {exc}")
        print("FINAL: FAIL")
        return 1

    print(json.dumps(result, indent=2))
    print("PASS: runtime monitoring startup completed")
    print("FINAL: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
