# PI-SCANNER analog activity event persistence and history queries.

from __future__ import annotations

import argparse
import json
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ACTIVITY_DIR = PROJECT_ROOT / "runtime" / "activity"
DEFAULT_STATUS_DIR = PROJECT_ROOT / "runtime" / "status"
VALID_ROLES = ("analog_2m", "analog_70cm")
MAX_EVENTS_PER_ROLE = 1000


class AnalogActivityError(RuntimeError):
    pass


def activity_log_path(
    role: str,
    activity_dir: Path = DEFAULT_ACTIVITY_DIR,
) -> Path:
    if role not in VALID_ROLES:
        raise AnalogActivityError(f"unsupported analog role: {role}")
    return Path(activity_dir) / f"{role}.jsonl"


def new_activity_event(
    role: str,
    rtl_serial: str,
    channel: dict[str, Any],
    start_utc: float | None = None,
) -> dict[str, Any]:
    start = float(start_utc if start_utc is not None else time.time())
    return {
        "event_id": f"{role}-{int(start * 1000)}-{uuid.uuid4().hex[:8]}",
        "role": role,
        "rtl_serial": str(rtl_serial),
        "channel_id": str(channel.get("id") or ""),
        "channel_name": str(
            channel.get("name")
            or channel.get("label")
            or channel.get("frequency_hz")
            or ""
        ),
        "frequency_hz": int(channel.get("frequency_hz") or 0),
        "mode": str(channel.get("mode") or "nfm"),
        "start_utc": start,
        "end_utc": None,
        "duration_seconds": None,
        "peak_rms": 0,
        "active_frames": 0,
        "end_reason": "",
        "ctcss_hz": channel.get("ctcss_hz"),
        "dcs_code": str(channel.get("dcs_code") or ""),
        "recording_enabled": bool(channel.get("recording_enabled", False)),
    }


def complete_activity_event(
    event: dict[str, Any],
    end_utc: float | None = None,
    end_reason: str = "squelch_closed",
) -> dict[str, Any]:
    completed = dict(event)
    end = float(end_utc if end_utc is not None else time.time())
    start = float(completed.get("start_utc") or end)
    completed["end_utc"] = end
    completed["duration_seconds"] = round(max(0.0, end - start), 3)
    completed["end_reason"] = str(end_reason)
    return completed


def append_completed_event(
    event: dict[str, Any],
    activity_dir: Path = DEFAULT_ACTIVITY_DIR,
    max_events: int = MAX_EVENTS_PER_ROLE,
) -> Path:
    role = str(event.get("role") or "")
    path = activity_log_path(role, activity_dir=activity_dir)
    if event.get("end_utc") is None:
        raise AnalogActivityError("activity event must be completed before append")
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    trim_activity_log(path, max_events=max_events)
    return path


def trim_activity_log(path: Path, max_events: int = MAX_EVENTS_PER_ROLE) -> None:
    if max_events <= 0 or not path.exists():
        return
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= max_events:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "\n".join(lines[-max_events:]) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_activity_events(
    role: str | None = None,
    limit: int = 100,
    activity_dir: Path = DEFAULT_ACTIVITY_DIR,
) -> list[dict[str, Any]]:
    roles = [role] if role else list(VALID_ROLES)
    events: list[dict[str, Any]] = []
    for item_role in roles:
        path = activity_log_path(item_role, activity_dir=activity_dir)
        if not path.exists():
            continue
        for line in path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
    events.sort(
        key=lambda item: float(item.get("end_utc") or item.get("start_utc") or 0),
        reverse=True,
    )
    return events[: max(1, min(int(limit), 1000))]


def read_current_activity(
    role: str,
    status_dir: Path = DEFAULT_STATUS_DIR,
) -> dict[str, Any] | None:
    path = Path(status_dir) / f"{role}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    current = payload.get("current_activity")
    return current if isinstance(current, dict) else None


def activity_payload(
    limit: int = 100,
    activity_dir: Path = DEFAULT_ACTIVITY_DIR,
    status_dir: Path = DEFAULT_STATUS_DIR,
) -> dict[str, Any]:
    events = read_activity_events(
        limit=limit,
        activity_dir=activity_dir,
    )
    current = {
        role: read_current_activity(role, status_dir=status_dir)
        for role in VALID_ROLES
    }
    counts = {role: 0 for role in VALID_ROLES}
    total_duration = {role: 0.0 for role in VALID_ROLES}
    for event in events:
        role = str(event.get("role") or "")
        if role in counts:
            counts[role] += 1
            total_duration[role] += float(event.get("duration_seconds") or 0.0)
    return {
        "ok": True,
        "updated_utc": time.time(),
        "history_limit": int(limit),
        "activity_dir": str(activity_dir),
        "current": current,
        "events": events,
        "summary": {
            role: {
                "event_count_in_window": counts[role],
                "duration_seconds_in_window": round(total_duration[role], 3),
                "active": current[role] is not None,
            }
            for role in VALID_ROLES
        },
    }


def clear_activity_history(
    role: str | None = None,
    activity_dir: Path = DEFAULT_ACTIVITY_DIR,
) -> dict[str, Any]:
    roles = [role] if role else list(VALID_ROLES)
    removed: list[str] = []
    for item_role in roles:
        path = activity_log_path(item_role, activity_dir=activity_dir)
        if path.exists():
            path.unlink()
            removed.append(str(path))
    return {
        "ok": True,
        "cleared_roles": roles,
        "removed_paths": removed,
        "updated_utc": time.time(),
    }


def emit_test_event(
    role: str,
    activity_dir: Path = DEFAULT_ACTIVITY_DIR,
) -> dict[str, Any]:
    channel = {
        "id": f"{role}-phase6-test",
        "name": "Phase 6 validation event",
        "frequency_hz": 146_520_000 if role == "analog_2m" else 446_000_000,
        "mode": "nfm",
        "ctcss_hz": None,
        "dcs_code": "",
        "recording_enabled": False,
    }
    serial = "00000440" if role == "analog_2m" else "00000144"
    event = new_activity_event(
        role,
        serial,
        channel,
        start_utc=time.time() - 1.25,
    )
    event["peak_rms"] = 4321
    event["active_frames"] = 50
    completed = complete_activity_event(
        event,
        end_reason="phase6_validation",
    )
    append_completed_event(completed, activity_dir=activity_dir)
    return completed


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="pi_scanner_activity_") as tmp:
        root = Path(tmp)
        activity_dir = root / "activity"
        status_dir = root / "status"
        status_dir.mkdir()
        event = emit_test_event("analog_2m", activity_dir=activity_dir)
        payload = activity_payload(
            limit=10,
            activity_dir=activity_dir,
            status_dir=status_dir,
        )
        checks = [
            len(payload["events"]) == 1,
            payload["events"][0]["event_id"] == event["event_id"],
            payload["events"][0]["duration_seconds"] > 1.0,
            payload["summary"]["analog_2m"]["event_count_in_window"] == 1,
        ]
        cleared = clear_activity_history(activity_dir=activity_dir)
        checks.append(bool(cleared["removed_paths"]))
        checks.append(not read_activity_events(activity_dir=activity_dir))
        if not all(checks):
            print(json.dumps({"payload": payload, "cleared": cleared}, indent=2))
            print("FINAL: FAIL")
            return 1
    print("PASS: analog activity history self-test")
    print("FINAL: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PI-SCANNER analog activity history")
    parser.add_argument("--role", choices=VALID_ROLES)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--emit-test-event", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.clear:
        result = clear_activity_history(role=args.role)
    elif args.emit_test_event:
        if not args.role:
            raise AnalogActivityError("--emit-test-event requires --role")
        result = emit_test_event(args.role)
    else:
        result = activity_payload(limit=args.limit)
    print(json.dumps(result, indent=2) if args.json or isinstance(result, dict) else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
