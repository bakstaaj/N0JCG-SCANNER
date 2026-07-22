# PI-SCANNER normalized activity view across P25 and analog receivers.

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

SOURCE_LABELS = {
    "p25_control": "P25",
    "p25_voice": "P25 Voice",
    "analog_2m": "2 m",
    "analog_70cm": "70 cm",
}
DEFAULT_SOURCE_PRIORITY_ORDER = (
    "p25_voice",
    "p25_control",
    "analog_2m",
    "analog_70cm",
)


def _float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _signaling_text(event: dict[str, Any]) -> str:
    detected_ctcss = event.get("detected_ctcss_hz")
    configured_ctcss = event.get("configured_ctcss_hz") or event.get("ctcss_hz")
    detected_dcs = event.get("detected_dcs_code")
    configured_dcs = event.get("configured_dcs_code") or event.get("dcs_code")
    if detected_ctcss is not None:
        return f"CTCSS {float(detected_ctcss):.1f} Hz"
    if detected_dcs:
        polarity = str(event.get("detected_dcs_polarity") or "")
        return f"DCS {detected_dcs}{(' ' + polarity) if polarity else ''}"
    if configured_ctcss is not None:
        suffix = " gate" if event.get("ctcss_gate_required") else ""
        return f"CTCSS {float(configured_ctcss):.1f} Hz{suffix}"
    if configured_dcs:
        suffix = " gate" if event.get("dcs_gate_required") else ""
        return f"DCS {configured_dcs}{suffix}"
    return ""


def normalize_analog_event(event: dict[str, Any]) -> dict[str, Any]:
    role = str(event.get("role") or "")
    return {
        "event_id": str(event.get("event_id") or ""),
        "source": role,
        "source_label": SOURCE_LABELS.get(role, role),
        "protocol": "analog",
        "started_utc": _float(event.get("start_utc")),
        "ended_utc": _float(event.get("end_utc") or event.get("start_utc")),
        "duration_seconds": (
            None
            if event.get("duration_seconds") is None
            else round(_float(event.get("duration_seconds")), 3)
        ),
        "channel_id": str(event.get("channel_id") or ""),
        "channel_label": str(
            event.get("channel_name")
            or event.get("channel_id")
            or "Analog activity"
        ),
        "frequency_hz": _int_or_none(event.get("frequency_hz")),
        "mode": str(event.get("mode") or "").upper(),
        "talkgroup_id": None,
        "p25_phase": "",
        "encrypted": False,
        "muted": False,
        "signaling": _signaling_text(event),
        "recording_url": event.get("recording_url"),
        "recording_filename": event.get("recording_filename"),
        "recording_size_bytes": event.get("recording_size_bytes"),
        "playable": bool(event.get("recording_url")),
        "peak_rms": event.get("peak_rms"),
        "end_reason": str(event.get("end_reason") or ""),
    }


def normalize_p25_events(
    p25_status: dict[str, Any],
    limit: int = 50,
) -> list[dict[str, Any]]:
    activity = p25_status.get("activity_summary") or {}
    raw_events = activity.get("recent_events") or []
    labels = (p25_status.get("talkgroup_catalog") or {}).get("labels") or {}
    normalized: list[dict[str, Any]] = []
    seen: list[tuple[tuple[Any, ...], float]] = []

    for reverse_index, event in enumerate(reversed(raw_events)):
        if not isinstance(event, dict):
            continue
        tgid = _int_or_none(event.get("tgid"))
        voice_hz = _int_or_none(event.get("voice_frequency_hz"))
        if tgid is None and voice_hz is None:
            continue
        updated = _float(event.get("updated_utc"))
        encrypted = event.get("encrypted")
        muted = event.get("muted")
        key = (tgid, voice_hz, encrypted, muted)
        if any(previous_key == key and abs(updated - when) <= 2.0 for previous_key, when in seen):
            continue
        seen.append((key, updated))

        configured_label = labels.get(str(tgid), "") if tgid is not None else ""
        label = str(
            event.get("talkgroup_label")
            or configured_label
            or (f"TGID {tgid}" if tgid is not None else "P25 voice")
        )
        normalized.append(
            {
                "event_id": (
                    f"p25-{int(updated * 1000)}-"
                    f"{tgid if tgid is not None else 'voice'}-{reverse_index}"
                ),
                "source": "p25_control",
                "source_label": "P25",
                "protocol": "p25",
                "started_utc": updated,
                "ended_utc": updated,
                "duration_seconds": None,
                "channel_id": str(tgid or ""),
                "channel_label": label,
                "frequency_hz": voice_hz,
                "mode": "P25",
                "talkgroup_id": tgid,
                "p25_phase": str(event.get("p25_phase") or ""),
                "encrypted": encrypted,
                "muted": muted,
                "signaling": "",
                "recording_url": None,
                "recording_filename": None,
                "recording_size_bytes": None,
                "playable": False,
                "peak_rms": None,
                "end_reason": "",
            }
        )
        if len(normalized) >= limit:
            break
    return normalized


def _workers_by_role(analog_status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("role") or ""): item
        for item in analog_status.get("workers") or []
        if isinstance(item, dict)
    }


def _active_descriptor(
    owner: str | None,
    p25_status: dict[str, Any],
    analog_activity: dict[str, Any],
) -> dict[str, Any] | None:
    if not owner:
        return None
    if owner.startswith("p25"):
        tgid = _int_or_none(p25_status.get("active_tgid"))
        label = str(
            p25_status.get("active_talkgroup_label")
            or p25_status.get("last_active_talkgroup_label")
            or (f"TGID {tgid}" if tgid is not None else "P25 audio")
        )
        return {
            "source": owner,
            "source_label": SOURCE_LABELS.get(owner, "P25"),
            "protocol": "p25",
            "channel_label": label,
            "talkgroup_id": tgid,
            "frequency_hz": _int_or_none(
                p25_status.get("active_voice_frequency_hz")
                or p25_status.get("last_active_voice_frequency_hz")
            ),
            "signaling": str(p25_status.get("p25_phase") or ""),
            "started_utc": None,
        }

    current = (analog_activity.get("current") or {}).get(owner)
    if isinstance(current, dict):
        return {
            "source": owner,
            "source_label": SOURCE_LABELS.get(owner, owner),
            "protocol": "analog",
            "channel_label": str(
                current.get("channel_name")
                or current.get("channel_id")
                or "Analog audio"
            ),
            "talkgroup_id": None,
            "frequency_hz": _int_or_none(current.get("frequency_hz")),
            "signaling": _signaling_text(current),
            "started_utc": _float(current.get("start_utc")) or None,
        }
    return {
        "source": owner,
        "source_label": SOURCE_LABELS.get(owner, owner),
        "protocol": "unknown",
        "channel_label": "Audio active",
        "talkgroup_id": None,
        "frequency_hz": None,
        "signaling": "",
        "started_utc": None,
    }


def build_unified_activity(
    p25_status: dict[str, Any],
    analog_status: dict[str, Any],
    analog_activity: dict[str, Any],
    limit: int = 75,
) -> dict[str, Any]:
    audio = analog_status.get("audio_arbiter") or {}
    owner = audio.get("active_source")
    analog_events = [
        normalize_analog_event(event)
        for event in analog_activity.get("events") or []
        if isinstance(event, dict)
    ]
    p25_events = normalize_p25_events(p25_status, limit=limit)
    history = analog_events + p25_events
    history.sort(
        key=lambda event: _float(
            event.get("ended_utc") or event.get("started_utc")
        ),
        reverse=True,
    )
    history = history[: max(1, min(int(limit), 500))]

    workers = _workers_by_role(analog_status)
    source_priority_order = audio.get("source_priority_order")
    if not isinstance(source_priority_order, list) or not source_priority_order:
        source_priority_order = list(DEFAULT_SOURCE_PRIORITY_ORDER)

    audio_sources = audio.get("sources") or {}
    source_health: list[dict[str, Any]] = []
    for source in source_priority_order:
        if source.startswith("p25"):
            service_active = bool(
                (p25_status.get("decoder_process") or {}).get("running")
            )
            runtime_state = str(p25_status.get("scanner_state") or "stopped")
        else:
            worker = workers.get(source) or {}
            service_active = bool((worker.get("service") or {}).get("active"))
            runtime_state = str(
                (worker.get("runtime") or {}).get("state")
                or (worker.get("service") or {}).get("active_state")
                or "stopped"
            )
        source_stats = audio_sources.get(source) or {}
        source_health.append(
            {
                "source": source,
                "source_label": SOURCE_LABELS.get(source, source),
                "service_active": service_active,
                "runtime_state": runtime_state,
                "audio_packets": int(source_stats.get("audio_packets") or 0),
                "accepted_frames": int(source_stats.get("accepted_frames") or 0),
                "dropped_non_owner_frames": int(
                    source_stats.get("dropped_non_owner_frames") or 0
                ),
                "last_active_age_seconds": source_stats.get(
                    "last_active_age_seconds"
                ),
                "priority": source_stats.get("priority"),
                "is_audio_owner": source == owner,
            }
        )

    analog_count = sum(1 for event in history if event["protocol"] == "analog")
    p25_count = sum(1 for event in history if event["protocol"] == "p25")
    recorded_count = sum(1 for event in history if event.get("playable"))

    return {
        "ok": bool(p25_status.get("ok", True)) and bool(
            analog_status.get("ok", True)
        ),
        "updated_utc": time.time(),
        "active_source": owner,
        "active": _active_descriptor(
            str(owner) if owner else None,
            p25_status,
            analog_activity,
        ),
        "policy": {
            "mode": str(
                audio.get("mode")
                or "current-transmission-wins-priority-tiebreak"
            ),
            "preemption_enabled": False,
            "current_transmission_holds_until_release": True,
            "release_seconds": audio.get("release_seconds"),
            "acquisition_grace_ms": audio.get("acquisition_grace_ms"),
            "source_priority_order": source_priority_order,
        },
        "source_health": source_health,
        "summary": {
            "history_count": len(history),
            "p25_events": p25_count,
            "analog_events": analog_count,
            "recorded_events": recorded_count,
            "audio_source_switches": int(audio.get("source_switches") or 0),
            "stream_clients": int(audio.get("stream_clients") or 0),
        },
        "history": history,
    }


def self_test() -> int:
    now = time.time()
    p25_status = {
        "ok": True,
        "scanner_state": "running",
        "decoder_process": {"running": True},
        "active_tgid": 1234,
        "active_talkgroup_label": "Fire Dispatch",
        "active_voice_frequency_hz": 851_012_500,
        "p25_phase": "Phase I",
        "talkgroup_catalog": {"labels": {"1234": "Fire Dispatch"}},
        "activity_summary": {
            "recent_events": [
                {
                    "updated_utc": now - 4,
                    "voice_frequency_hz": 851_012_500,
                    "tgid": 1234,
                    "talkgroup_label": "Fire Dispatch",
                    "p25_phase": "Phase I",
                    "encrypted": False,
                    "muted": False,
                }
            ]
        },
    }
    analog_status = {
        "ok": True,
        "workers": [
            {
                "role": "analog_2m",
                "service": {"active": True},
                "runtime": {"state": "active"},
            },
            {
                "role": "analog_70cm",
                "service": {"active": True},
                "runtime": {"state": "scanning"},
            },
        ],
        "audio_arbiter": {
            "ok": True,
            "mode": "current-transmission-wins-priority-tiebreak-v0.6i",
            "active_source": "analog_2m",
            "release_seconds": 0.75,
            "acquisition_grace_ms": 40.0,
            "source_priority_order": list(DEFAULT_SOURCE_PRIORITY_ORDER),
            "sources": {
                source: {
                    "audio_packets": 1,
                    "accepted_frames": 1 if source == "analog_2m" else 0,
                    "dropped_non_owner_frames": 0,
                    "priority": 400 - index * 100,
                }
                for index, source in enumerate(DEFAULT_SOURCE_PRIORITY_ORDER)
            },
        },
    }
    analog_activity = {
        "ok": True,
        "current": {
            "analog_2m": {
                "channel_name": "Local Repeater",
                "frequency_hz": 146_940_000,
                "start_utc": now - 2,
                "configured_ctcss_hz": 100.0,
                "detected_ctcss_hz": 100.0,
            },
            "analog_70cm": None,
        },
        "events": [
            {
                "event_id": "analog-test",
                "role": "analog_70cm",
                "channel_name": "UHF Simplex",
                "frequency_hz": 446_000_000,
                "mode": "nfm",
                "start_utc": now - 8,
                "end_utc": now - 7,
                "duration_seconds": 1.0,
                "peak_rms": 2200,
                "recording_url": "/api/analog/recordings/file?role=analog_70cm&filename=test.wav",
                "recording_filename": "test.wav",
            }
        ],
    }
    payload = build_unified_activity(
        p25_status,
        analog_status,
        analog_activity,
    )
    checks = [
        payload["active_source"] == "analog_2m",
        payload["active"]["channel_label"] == "Local Repeater",
        payload["policy"]["preemption_enabled"] is False,
        payload["policy"]["source_priority_order"][0] == "p25_voice",
        payload["summary"]["p25_events"] == 1,
        payload["summary"]["analog_events"] == 1,
        payload["summary"]["recorded_events"] == 1,
        len(payload["source_health"]) == 4,
        len(payload["history"]) == 2,
    ]
    if not all(checks):
        print(json.dumps({"payload": payload, "checks": checks}, indent=2))
        print("FINAL: FAIL")
        return 1
    print(json.dumps(payload, indent=2))
    print("PASS: unified activity self-test")
    print("FINAL: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PI-SCANNER unified activity model"
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    parser.error("no action selected")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
