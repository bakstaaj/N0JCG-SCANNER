# CSV import for P25 control, voice, and talkgroup configuration.

from __future__ import annotations

import csv
import io
import time
from typing import Any

from .config_model import ConfigError, frequency_to_hz
from .config_store import (
    read_active_config_payload,
    validate_config_payload,
    write_runtime_config,
)

MAX_CSV_BYTES = 512 * 1024
MAX_ROWS = 5000
HEADERS = (
    "RecordType",
    "System",
    "Site",
    "FrequencyMHz",
    "TGID",
    "Name",
    "Enabled",
    "Priority",
    "ServiceType",
    "NAC",
    "Modulation",
    "ControlDemod",
    "Description",
)
REQUIRED = {"RecordType", "System"}


class P25CsvError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, default: bool = True) -> bool:
    text = _text(value).lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled", "skip"}:
        return False
    raise P25CsvError(f"invalid Enabled value {value!r}")


def _record_type(value: Any) -> str:
    text = _text(value).lower().replace("_", "").replace("-", "").replace(" ", "")
    aliases = {
        "control": "control",
        "controlchannel": "control",
        "cc": "control",
        "voice": "voice",
        "voicechannel": "voice",
        "vc": "voice",
        "talkgroup": "talkgroup",
        "tg": "talkgroup",
        "tgid": "talkgroup",
    }
    if text not in aliases:
        raise P25CsvError(
            f"RecordType must be control, voice, or talkgroup; got {value!r}"
        )
    return aliases[text]


def _priority(value: Any) -> int:
    text = _text(value)
    if not text:
        return 0
    number = int(text)
    if not 0 <= number <= 100:
        raise P25CsvError("Priority must be 0 through 100")
    return number


def parse_p25_csv(text: str) -> dict[str, Any]:
    if not isinstance(text, str):
        raise P25CsvError("csv_text must be a string")
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise P25CsvError("CSV is too large")

    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    headers = set(reader.fieldnames or [])
    if not headers:
        raise P25CsvError("CSV header row is missing")
    missing = sorted(REQUIRED - headers)
    if missing:
        raise P25CsvError("Missing required columns: " + ", ".join(missing))

    systems: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    warnings: list[str] = []
    row_count = 0

    for row_number, row in enumerate(reader, start=2):
        if row_number - 1 > MAX_ROWS:
            errors.append(f"row {row_number}: more than {MAX_ROWS} records")
            break
        if not any(_text(value) for value in row.values()):
            continue
        row_count += 1
        try:
            kind = _record_type(row.get("RecordType"))
            system_name = _text(row.get("System"))
            if not system_name:
                raise P25CsvError("System is required")
            system = systems.setdefault(
                system_name,
                {
                    "name": system_name,
                    "site": _text(row.get("Site")),
                    "nac": _text(row.get("NAC")) or None,
                    "modulation": _text(row.get("Modulation")) or "CQPSK",
                    "control_demod_type": _text(row.get("ControlDemod")) or "fsk4",
                    "control_channels_hz": [],
                    "voice_channels_hz": [],
                    "talkgroups": [],
                    "_control_seen": set(),
                    "_voice_seen": set(),
                    "_tgid_seen": set(),
                },
            )

            site = _text(row.get("Site"))
            nac = _text(row.get("NAC"))
            modulation = _text(row.get("Modulation"))
            if site:
                system["site"] = site
            if nac:
                system["nac"] = nac
            if modulation:
                system["modulation"] = modulation
            control_demod = _text(row.get("ControlDemod"))
            if control_demod:
                system["control_demod_type"] = control_demod.lower()

            enabled = _bool(row.get("Enabled"), True)

            if kind in {"control", "voice"}:
                frequency = _text(row.get("FrequencyMHz"))
                if not frequency:
                    raise P25CsvError(f"FrequencyMHz is required for {kind}")
                hz = frequency_to_hz(frequency)
                seen_key = "_control_seen" if kind == "control" else "_voice_seen"
                target_key = (
                    "control_channels_hz"
                    if kind == "control"
                    else "voice_channels_hz"
                )
                if hz in system[seen_key]:
                    warnings.append(
                        f"row {row_number}: duplicate {kind} frequency skipped"
                    )
                    continue
                system[seen_key].add(hz)
                if enabled:
                    system[target_key].append(hz)
                continue

            tgid_text = _text(row.get("TGID"))
            if not tgid_text:
                raise P25CsvError("TGID is required for talkgroup")
            tgid = int(tgid_text, 0)
            if not 0 <= tgid <= 65535:
                raise P25CsvError("TGID must be 0 through 65535")
            if tgid in system["_tgid_seen"]:
                raise P25CsvError(f"duplicate TGID {tgid}")
            system["_tgid_seen"].add(tgid)

            label = _text(row.get("Name")) or f"TGID {tgid}"
            talkgroup = {
                "tgid": tgid,
                "label": label[:120],
                "enabled": enabled,
            }
            priority = _priority(row.get("Priority"))
            service_type = _text(row.get("ServiceType"))
            description = _text(row.get("Description"))
            if priority:
                talkgroup["priority"] = priority
            if service_type:
                talkgroup["service_type"] = service_type
            if description:
                talkgroup["description"] = description
            system["talkgroups"].append(talkgroup)
        except (P25CsvError, ConfigError, TypeError, ValueError) as exc:
            errors.append(f"row {row_number}: {exc}")

    if errors:
        raise P25CsvError("; ".join(errors[:30]))
    if not systems:
        raise P25CsvError("CSV contains no usable P25 records")

    for system in systems.values():
        for key in ("_control_seen", "_voice_seen", "_tgid_seen"):
            system.pop(key, None)
        if not system["control_channels_hz"]:
            raise P25CsvError(
                f"System {system['name']!r} must contain at least one enabled control row"
            )

    return {
        "systems": list(systems.values()),
        "row_count": row_count,
        "warnings": warnings,
    }


def import_p25_csv_request(request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise P25CsvError("request must be an object")
    parsed = parse_p25_csv(request.get("csv_text"))
    replace_mode = _text(request.get("replace_mode")).lower() or "systems_in_file"
    if replace_mode not in {"systems_in_file", "all", "append"}:
        raise P25CsvError("invalid replace_mode")

    payload, _config_path = read_active_config_payload()
    existing_systems = list(payload.get("systems") or [])
    existing_by_name = {
        _text(item.get("name")).lower(): item
        for item in existing_systems
        if isinstance(item, dict)
    }

    incoming_names = {_text(item["name"]).lower() for item in parsed["systems"]}
    output: list[dict[str, Any]] = []

    if replace_mode != "all":
        output.extend(
            item
            for item in existing_systems
            if isinstance(item, dict)
            and (
                replace_mode == "append"
                or _text(item.get("name")).lower() not in incoming_names
            )
        )

    for incoming in parsed["systems"]:
        key = _text(incoming["name"]).lower()
        existing = existing_by_name.get(key, {})
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.update(
            {
                "name": incoming["name"],
                "enabled": True,
                "mode": "p25_trunked",
                "site": incoming["site"],
                "control_channels_hz": incoming["control_channels_hz"],
                "voice_channels_hz": incoming["voice_channels_hz"],
                "talkgroups": incoming["talkgroups"],
                "nac": incoming["nac"],
                "modulation": incoming["modulation"],
                "control_demod_type": incoming["control_demod_type"],
            }
        )
        merged.setdefault("receiver_roles", existing.get("receiver_roles", {}))
        merged.setdefault("decoder", existing.get("decoder", {}))

        if replace_mode == "append" and existing:
            merged["control_channels_hz"] = list(
                dict.fromkeys(
                    list(existing.get("control_channels_hz") or [])
                    + incoming["control_channels_hz"]
                )
            )
            merged["voice_channels_hz"] = list(
                dict.fromkeys(
                    list(existing.get("voice_channels_hz") or [])
                    + incoming["voice_channels_hz"]
                )
            )
            tg_by_id = {
                int(item["tgid"]): dict(item)
                for item in existing.get("talkgroups", [])
                if isinstance(item, dict) and "tgid" in item
            }
            for item in incoming["talkgroups"]:
                tg_by_id[int(item["tgid"])] = dict(item)
            merged["talkgroups"] = list(tg_by_id.values())

        output.append(merged)

    payload["schema_version"] = max(1, int(payload.get("schema_version") or 1))
    payload["systems"] = output
    payload["last_p25_csv_import"] = {
        "filename": _text(request.get("filename")) or "p25_import.csv",
        "imported_utc": time.time(),
        "replace_mode": replace_mode,
        "row_count": parsed["row_count"],
        "systems": [item["name"] for item in parsed["systems"]],
    }

    validate_config_payload(payload)
    write_runtime_config(payload)
    return {
        "ok": True,
        "imported_rows": parsed["row_count"],
        "systems": [item["name"] for item in parsed["systems"]],
        "warnings": parsed["warnings"],
        "replace_mode": replace_mode,
    }


# Compatibility for the route currently importing these names from analog_channels.
# This avoids changing backend.py and avoids a circular import.
try:
    from . import analog_channels as _analog_channels

    _analog_channels.P25CsvError = P25CsvError
    _analog_channels.import_p25_csv_request = import_p25_csv_request
except Exception:
    pass
