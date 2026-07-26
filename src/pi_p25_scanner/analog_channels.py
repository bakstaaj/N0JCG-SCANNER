# CSV-backed 2 m and 70 cm channel configuration for PI-SCANNER.

from __future__ import annotations
from pi_p25_scanner.chirp_csv_import import ChirpCsvError, normalize_chirp_rows

import csv
import io
import json
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "runtime" / "settings" / "analog_receivers.json"
DEFAULT_TEMPLATE_PATH = PROJECT_ROOT / "config" / "analog_receivers.example.json"
MAX_CSV_BYTES = 512 * 1024
MAX_ROWS = 2000

ROLE_DEFAULTS = {
    "analog_2m": {
        "enabled": False, "rtl_serial": "00000440", "modulation": "fm",
        "gain_db": 40.2, "ppm": 0, "sample_rate_hz": 24000,
        "audio_rate_hz": 8000, "audio_udp_port": 23458, "frame_bytes": 320,
        "dwell_seconds": 1.0, "hang_seconds": 0.9,
        "resume_delay_seconds": 1.2, "squelch_rms": 1800, "channels": [],
    },
    "analog_70cm": {
        "enabled": False, "rtl_serial": "00000144", "modulation": "fm",
        "gain_db": 40.2, "ppm": 0, "sample_rate_hz": 24000,
        "audio_rate_hz": 8000, "audio_udp_port": 23459, "frame_bytes": 320,
        "dwell_seconds": 1.0, "hang_seconds": 0.9,
        "resume_delay_seconds": 1.2, "squelch_rms": 1800, "channels": [],
    },
}
ROLE_ALIASES = {
    "2m": "analog_2m", "2 m": "analog_2m", "vhf": "analog_2m",
    "analog 2m": "analog_2m", "analog_2m": "analog_2m",
    "70cm": "analog_70cm", "70 cm": "analog_70cm", "uhf": "analog_70cm",
    "analog 70cm": "analog_70cm", "analog_70cm": "analog_70cm",
}
HEADER_ALIASES = {
    "band": "receiver", "role": "receiver", "receiver_role": "receiver",
    "alpha_tag": "name", "label": "name", "description": "name",
    "frequency": "frequency_mhz", "freq": "frequency_mhz",
    "freq_mhz": "frequency_mhz", "ctcss": "ctcss_hz", "pl": "ctcss_hz",
    "dcs": "dcs_code", "dpl": "dcs_code", "record": "recording_enabled",
}


class AnalogChannelError(ValueError):
    pass


def _key(value: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return HEADER_ALIASES.get(key, key)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, default: bool = False) -> bool:
    text = _text(value).lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    raise AnalogChannelError(f"invalid boolean {value!r}")


def _num(value: Any, default: float) -> float:
    text = _text(value)
    return default if not text else float(text)


def _optional_num(value: Any) -> float | None:
    text = _text(value)
    return None if not text else float(text)


def _role(value: Any) -> str:
    raw = _text(value).lower()
    normalized = re.sub(r"\s+", " ", raw.replace("-", " ").replace("_", " ")).strip()
    role = ROLE_ALIASES.get(raw) or ROLE_ALIASES.get(normalized)
    if not role:
        raise AnalogChannelError(f"unsupported receiver {value!r}")
    return role


def _mode(value: Any) -> str:
    raw = _text(value).lower().replace("-", "").replace("_", "")
    modes = {"": "fm", "fm": "fm", "widefm": "fm", "nfm": "nfm",
             "narrowfm": "nfm", "fmn": "nfm", "am": "am"}
    if raw not in modes:
        raise AnalogChannelError(f"mode must be FM, NFM, or AM; got {value!r}")
    return modes[raw]


def _frequency(row: dict[str, str]) -> int:
    if _text(row.get("frequency_hz")):
        hz = int(round(float(_text(row["frequency_hz"]).replace(",", "").replace("_", ""))))
    elif _text(row.get("frequency_mhz")):
        mhz = _text(row["frequency_mhz"]).lower().replace("mhz", "").replace(",", "").replace("_", "")
        hz = int(round(float(mhz) * 1_000_000))
    else:
        raise AnalogChannelError("missing frequency_mhz")
    if not 24_000_000 <= hz <= 1_766_000_000:
        raise AnalogChannelError(f"frequency outside RTL-SDR range: {hz}")
    return hz


def default_config() -> dict[str, Any]:
    return {
        "schema_version": 5, "audio_udp_host": "127.0.0.1",
        "source": "analog_csv_import",
        "workers": json.loads(json.dumps(ROLE_DEFAULTS)),
    }


def load_config(
    path: Path = DEFAULT_CONFIG_PATH,
    template_path: Path = DEFAULT_TEMPLATE_PATH,
) -> dict[str, Any]:
    path = Path(path)
    source_path = path if path.exists() else Path(template_path)
    if not source_path.exists():
        return default_config()
    try:
        data = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AnalogChannelError(f"invalid analog config JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AnalogChannelError("analog config must be an object")
    workers = data.setdefault("workers", {})
    if not isinstance(workers, dict):
        raise AnalogChannelError("workers must be an object")
    for role, defaults in ROLE_DEFAULTS.items():
        worker = workers.setdefault(role, json.loads(json.dumps(defaults)))
        for key, value in defaults.items():
            worker.setdefault(key, json.loads(json.dumps(value)))
    data["schema_version"] = max(5, int(data.get("schema_version") or 0))
    return data


def parse_csv_text(text: str) -> dict[str, Any]:
    if not isinstance(text, str):
        raise AnalogChannelError("csv_text must be a string")
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise AnalogChannelError("CSV is too large")
    reader = normalize_chirp_rows(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    if not reader.fieldnames:
        raise AnalogChannelError("CSV header row is missing")
    headers = [_key(item) for item in reader.fieldnames]
    if "receiver" not in headers:
        raise AnalogChannelError("CSV requires receiver")
    if "frequency_mhz" not in headers and "frequency_hz" not in headers:
        raise AnalogChannelError("CSV requires frequency_mhz")

    result = {role: [] for role in ROLE_DEFAULTS}
    warnings: list[str] = []
    errors: list[str] = []
    seen: set[tuple[Any, ...]] = set()

    for row_number, raw in enumerate(reader, 2):
        if row_number - 1 > MAX_ROWS:
            errors.append(f"row {row_number}: more than {MAX_ROWS} channels")
            break
        row = {_key(k): _text(v) for k, v in raw.items() if k is not None}
        if not any(row.values()):
            continue
        try:
            role = _role(row.get("receiver"))
            hz = _frequency(row)
            name = _text(row.get("name")) or f"{hz / 1_000_000:.6f} MHz"
            ctcss = _optional_num(row.get("ctcss_hz"))
            if ctcss is not None and not 50.0 <= ctcss <= 300.0:
                raise AnalogChannelError("ctcss_hz must be 50.0 through 300.0")
            dcs = _text(row.get("dcs_code")).upper()
            if dcs and not re.fullmatch(r"[0-7]{3}(?:N|I)?", dcs):
                raise AnalogChannelError("invalid dcs_code")
            tone_gate = _bool(row.get("tone_gate"), False)
            dcs_gate = _bool(row.get("dcs_gate"), False)
            if tone_gate and ctcss is None:
                raise AnalogChannelError("tone_gate requires ctcss_hz")
            if dcs_gate and not dcs:
                raise AnalogChannelError("dcs_gate requires dcs_code")
            if tone_gate and dcs_gate:
                raise AnalogChannelError("tone_gate and dcs_gate cannot both be true")

            channel = {
                "id": f"{role}-{hz}-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:32]}",
                "enabled": _bool(row.get("enabled"), True),
                "name": name[:120], "frequency_hz": hz, "mode": _mode(row.get("mode")),
                "priority": max(0, min(100, int(_num(row.get("priority"), 0)))),
                "gain_db": _num(row.get("gain_db"), 40.2),
                "squelch_rms": max(0, int(_num(row.get("squelch_rms"), 1800))),
                "hold_seconds": max(0.1, min(30.0, _num(row.get("hold_seconds"), 0.9))),
                "resume_delay_seconds": max(0.0, min(30.0, _num(row.get("resume_delay_seconds"), 1.2))),
                "ctcss_hz": ctcss, "tone_gate": tone_gate,
                "dcs_code": dcs, "dcs_gate": dcs_gate,
                "recording_enabled": _bool(row.get("recording_enabled"), False),
            }
            duplicate = (role, hz, name.lower(), ctcss, dcs)
            if duplicate in seen:
                warnings.append(f"row {row_number}: skipped exact duplicate")
                continue
            seen.add(duplicate)
            result[role].append(channel)
            if role == "analog_2m" and not 136_000_000 <= hz <= 174_000_000:
                warnings.append(f"row {row_number}: frequency is outside usual VHF/2 m range")
            if role == "analog_70cm" and not 400_000_000 <= hz <= 520_000_000:
                warnings.append(f"row {row_number}: frequency is outside usual UHF/70 cm range")
        except (AnalogChannelError, TypeError, ValueError) as exc:
            errors.append(f"row {row_number}: {exc}")

    if errors:
        raise AnalogChannelError("; ".join(errors[:20]))
    count = sum(len(items) for items in result.values())
    if count == 0:
        raise AnalogChannelError("CSV contains no usable channels")
    return {
        "channels_by_role": result,
        "roles_present": [role for role, items in result.items() if items],
        "imported_rows": count,
        "warnings": warnings,
    }


def _write(path: Path, data: dict[str, Any]) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup_dir = path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{path.stem}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}{path.suffix}"
        shutil.copy2(path, backup)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(data, handle, indent=2)
        handle.write("\n")
    temporary.replace(path)
    return backup


def import_csv_request(request: dict[str, Any], config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise AnalogChannelError("request must be an object")
    parsed = parse_csv_text(request.get("csv_text"))
    mode = _text(request.get("replace_mode")).lower() or "roles_in_file"
    if mode not in {"roles_in_file", "all", "append"}:
        raise AnalogChannelError("invalid replace_mode")
    config = load_config(config_path)
    workers = config["workers"]
    if mode == "all":
        for worker in workers.values():
            worker["channels"] = []
    for role in parsed["roles_present"]:
        incoming = parsed["channels_by_role"][role]
        workers[role]["channels"] = (
            list(workers[role].get("channels") or []) + incoming
            if mode == "append" else incoming
        )
        workers[role]["enabled"] = bool(workers[role]["channels"])
    config["schema_version"] = 5
    config["source"] = "analog_csv_import"
    config["last_import"] = {
        "filename": _text(request.get("filename")) or "uploaded.csv",
        "imported_utc": time.time(), "replace_mode": mode,
        "row_count": parsed["imported_rows"], "roles": parsed["roles_present"],
    }
    backup = _write(Path(config_path), config)
    return {
        "ok": True, "config_path": str(config_path),
        "backup_path": str(backup) if backup else None,
        "filename": config["last_import"]["filename"],
        "replace_mode": mode, "imported_rows": parsed["imported_rows"],
        "roles_present": parsed["roles_present"],
        "channel_counts": {role: len(worker["channels"]) for role, worker in workers.items()},
        "warnings": parsed["warnings"],
    }


def channels_payload(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    config = load_config(config_path)
    workers = config["workers"]
    return {
        "ok": True, "config_path": str(config_path),
        "schema_version": config.get("schema_version"),
        "last_import": config.get("last_import"),
        "serial_bindings": {role: worker["rtl_serial"] for role, worker in workers.items()},
        "channel_counts": {role: len(worker["channels"]) for role, worker in workers.items()},
        "enabled_counts": {
            role: sum(1 for channel in worker["channels"] if channel.get("enabled", True))
            for role, worker in workers.items()
        },
        "workers": workers,
    }
