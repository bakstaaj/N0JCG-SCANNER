"""CSV export helpers for named PI-SCANNER radio profiles."""

from __future__ import annotations

import csv
import io
from typing import Any

from .chirp_csv_import import CHIRP_COLUMNS
from .config_model import normalize_control_demod
from .p25_csv_import import HEADERS as P25_HEADERS


def _text(value: Any) -> str:
    return str(value or "").strip()


def _control_demod_for_csv(value: Any) -> str:
    """Export OP25 demodulator values using terms users see in references."""
    normalized = normalize_control_demod(value)
    return {"fsk4": "C4FM", "cqpsk": "CQPSK"}.get(normalized, _text(value))


def _csv_text(headers: tuple[str, ...], rows: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def analog_channels_to_chirp_csv(
    channels_by_role: dict[str, Any] | None,
) -> str:
    """Export VHF/UHF channels using the standard CHIRP CSV columns."""

    rows: list[dict[str, Any]] = []
    location = 0
    channel_map = channels_by_role if isinstance(channels_by_role, dict) else {}
    for role in ("analog_2m", "analog_70cm"):
        channels = channel_map.get(role)
        if not isinstance(channels, list):
            continue
        for channel in channels:
            if not isinstance(channel, dict):
                continue
            frequency_hz = int(channel.get("frequency_hz") or 0)
            if frequency_hz <= 0:
                continue
            tone_hz = channel.get("ctcss_hz")
            tone_gate = bool(channel.get("tone_gate")) and tone_hz is not None
            dcs_code = _text(channel.get("dcs_code")) or "023"
            row = {header: "" for header in CHIRP_COLUMNS}
            row.update(
                {
                    "Location": location,
                    "Name": _text(channel.get("name")) or f"Channel {location}",
                    "Frequency": f"{frequency_hz / 1_000_000:.6f}",
                    "Duplex": "off",
                    "Offset": "0.000000",
                    "Tone": "TSQL" if tone_gate else "",
                    "rToneFreq": f"{float(tone_hz):.1f}" if tone_hz else "88.5",
                    "cToneFreq": f"{float(tone_hz):.1f}" if tone_hz else "88.5",
                    "DtcsCode": dcs_code.removesuffix("N").removesuffix("I"),
                    "DtcsPolarity": "NN",
                    "RxDtcsCode": dcs_code.removesuffix("N").removesuffix("I"),
                    "CrossMode": "Tone->Tone",
                    "Mode": "NFM" if _text(channel.get("mode")).lower() != "am" else "AM",
                    "TStep": "5.00",
                    "Skip": "" if channel.get("enabled", True) else "S",
                    "Comment": _text(channel.get("comment")),
                }
            )
            rows.append(row)
            location += 1
    return _csv_text(CHIRP_COLUMNS, rows)


def p25_config_to_csv(config: dict[str, Any] | None) -> str:
    """Export P25 systems, frequencies, and talkgroups using the app template."""

    rows: list[dict[str, Any]] = []
    payload = config if isinstance(config, dict) else {}
    systems = payload.get("systems")
    if not isinstance(systems, list):
        systems = []
    for system in systems:
        if not isinstance(system, dict):
            continue
        common = {
            "System": _text(system.get("name")),
            "Site": _text(system.get("site")),
            "NAC": _text(system.get("nac")),
            "Modulation": _text(system.get("modulation")) or "CQPSK",
            "ControlDemod": _control_demod_for_csv(system.get("control_demod_type")),
        }
        for record_type, key in (
            ("control", "control_channels_hz"),
            ("voice", "voice_channels_hz"),
        ):
            for frequency_hz in system.get(key) or []:
                row = {header: "" for header in P25_HEADERS}
                row.update(common)
                row.update(
                    {
                        "RecordType": record_type,
                        "FrequencyMHz": f"{int(frequency_hz) / 1_000_000:.6f}",
                        "Enabled": "true",
                    }
                )
                rows.append(row)
        for talkgroup in system.get("talkgroups") or []:
            if not isinstance(talkgroup, dict) or "tgid" not in talkgroup:
                continue
            row = {header: "" for header in P25_HEADERS}
            row.update(common)
            row.update(
                {
                    "RecordType": "talkgroup",
                    "TGID": int(talkgroup["tgid"]),
                    "Name": _text(talkgroup.get("label")),
                    "Enabled": "true" if talkgroup.get("enabled", True) else "false",
                    "Priority": talkgroup.get("priority") or "",
                    "ServiceType": _text(talkgroup.get("service_type")),
                    "Description": _text(talkgroup.get("description")),
                }
            )
            rows.append(row)
    return _csv_text(P25_HEADERS, rows)
