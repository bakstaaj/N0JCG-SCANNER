"""CHIRP CSV normalization for the PI-SCANNER analog-channel uploader.

The importer accepts the standard CHIRP CSV column layout and exposes
compatibility aliases used by the existing analog-channel import code.
It does not import repeater transmit offsets because PI-SCANNER is receive-only.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any

CHIRP_COLUMNS = (
    "Location",
    "Name",
    "Frequency",
    "Duplex",
    "Offset",
    "Tone",
    "rToneFreq",
    "cToneFreq",
    "DtcsCode",
    "DtcsPolarity",
    "RxDtcsCode",
    "CrossMode",
    "Mode",
    "TStep",
    "Skip",
    "Power",
    "Comment",
    "URCALL",
    "RPT1CALL",
    "RPT2CALL",
    "DVCODE",
)

_REQUIRED_COLUMNS = {"Location", "Name", "Frequency", "Mode"}


class ChirpCsvError(ValueError):
    """Raised when an uploaded CSV is not a supported CHIRP CSV file."""


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _parse_float(value: Any, *, field: str, row_number: int) -> float:
    text = _clean(value)
    try:
        return float(text)
    except (TypeError, ValueError) as exc:
        raise ChirpCsvError(
            f"CHIRP row {row_number}: {field} must be numeric; got {text!r}"
        ) from exc


def _receive_tone_hz(row: Mapping[str, Any], row_number: int) -> float | None:
    """Return a receive CTCSS tone only when CHIRP enables receive tone squelch."""

    tone_mode = _clean(row.get("Tone")).upper()
    cross_mode = _clean(row.get("CrossMode")).upper()

    # CHIRP "Tone" is transmit-only and should not gate a receive-only scanner.
    use_receive_tone = tone_mode in {"TSQL", "TSQL-R"}
    if tone_mode == "CROSS":
        use_receive_tone = cross_mode.endswith("->TONE")

    if not use_receive_tone:
        return None

    text = _clean(row.get("cToneFreq"))
    if not text:
        return None

    tone = _parse_float(text, field="cToneFreq", row_number=row_number)
    return tone if tone > 0 else None


def _normalized_mode(row: Mapping[str, Any]) -> str:
    """Normalize CHIRP FM/NFM channels to PI-SCANNER's single analog mode."""

    source_mode = _clean(row.get("Mode")).upper()
    if source_mode not in {"FM", "NFM"}:
        raise ChirpCsvError(
            f"Unsupported CHIRP Mode {source_mode!r}; analog upload supports FM/NFM"
        )
    # The native linear scanner requires one modulation mode per receiver.
    return "nfm"


def normalize_chirp_rows(
    reader: Iterable[Mapping[str, Any]],
) -> Iterator[dict[str, Any]]:
    """Validate CHIRP headers and yield rows with legacy compatibility aliases."""

    fieldnames = getattr(reader, "fieldnames", None)
    if fieldnames is None:
        raise ChirpCsvError("CSV is missing a header row")

    normalized_headers = {_clean(name) for name in fieldnames if name is not None}
    missing = sorted(_REQUIRED_COLUMNS - normalized_headers)
    if missing:
        raise ChirpCsvError(
            "Upload must use CHIRP CSV format. Missing columns: "
            + ", ".join(missing)
        )

    for row_number, source in enumerate(reader, start=2):
        if not isinstance(source, Mapping):
            raise ChirpCsvError(f"CHIRP row {row_number}: invalid row")

        name = _clean(source.get("Name"))
        frequency_text = _clean(source.get("Frequency"))

        # Ignore completely blank trailing rows.
        if not name and not frequency_text:
            continue
        if not frequency_text:
            raise ChirpCsvError(f"CHIRP row {row_number}: Frequency is required")

        frequency_mhz = _parse_float(
            frequency_text,
            field="Frequency",
            row_number=row_number,
        )
        if not 24.0 <= frequency_mhz <= 1300.0:
            raise ChirpCsvError(
                f"CHIRP row {row_number}: Frequency {frequency_mhz} MHz is out of range"
            )

        location = _clean(source.get("Location"))
        if not name:
            name = f"Channel {location or row_number - 1}"

        mode = _normalized_mode(source)
        tone_hz = _receive_tone_hz(source, row_number)
        skip_value = _clean(source.get("Skip")).upper()
        enabled = skip_value not in {"S", "L"}
        comment = _clean(source.get("Comment"))

        # Start with every original CHIRP field so diagnostics can display it.
        result: dict[str, Any] = {
            _clean(key): value for key, value in source.items() if key is not None
        }

        # Add the aliases commonly consumed by the existing uploader.
        result.update(
            {
                "name": name,
                "Name": name,
                "label": name,
                "Label": name,
                "description": comment,
                "Description": comment,
                "comment": comment,
                "Comment": comment,
                "frequency": frequency_mhz,
                "Frequency": frequency_mhz,
                "frequency_mhz": frequency_mhz,
                "FrequencyMHz": frequency_mhz,
                "frequency_hz": int(round(frequency_mhz * 1_000_000)),
                "FrequencyHz": int(round(frequency_mhz * 1_000_000)),
                "mode": mode,
                "Mode": mode,
                "modulation": mode,
                "Modulation": mode,
                "tone_hz": tone_hz,
                "ToneHz": tone_hz,
                "ctcss_hz": tone_hz,
                "CTCSS": tone_hz,
                "enabled": enabled,
                "Enabled": enabled,
                "skip": skip_value,
                "source_mode": _clean(source.get("Mode")).upper(),
                "source_location": location,
                "source_format": "chirp_csv",
            }
        )
        yield result
