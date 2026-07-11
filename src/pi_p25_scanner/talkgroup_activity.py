"""Best-effort OP25 talkgroup activity extraction for the PI P25 Scanner UI.

This module is intentionally read-only: it parses OP25/backend text already being
captured by the backend and converts it into dashboard-friendly activity fields.
It does not control OP25 and it does not gate or filter audio.
"""

from __future__ import annotations

import re
import time
from typing import Any

_CONFIG_LINE_TOKENS = (
    "whitelist",
    "blacklist",
    "whiteli",
    "blackli",
    "trunk.tsv",
    "configured talkgroup",
    "loading talkgroup",
    "loaded talkgroup",
    "added talkgroup",
    "adding talkgroup",
    "reading talkgroup",
    "from runtime/",
    " from /",
)

_TGID_PATTERNS = (
    re.compile(r"\bnew\s+tgid\s*[=:]\s*(?P<tgid>\d{2,7})\b", re.IGNORECASE),
    re.compile(r"\btgid\s*[=:]\s*(?P<tgid>\d{2,7})\b", re.IGNORECASE),
    re.compile(r"\btgid\s+(?P<tgid>\d{2,7})\b", re.IGNORECASE),
    re.compile(r"\btg(?:id)?\((?P<tgid>\d{2,7})\)", re.IGNORECASE),
    re.compile(r"\btg(?:id)?\s*[=:]\s*(?P<tgid>\d{2,7})\b", re.IGNORECASE),
    re.compile(r"\btalk\s*group\s*[=:]?\s*(?P<tgid>\d{2,7})\b", re.IGNORECASE),
    re.compile(r"\btalkgroup\s*[=:]?\s*(?P<tgid>\d{2,7})\b", re.IGNORECASE),
    re.compile(r"\bgrpaddr\s*[=:]\s*(?P<tgid>\d{2,7})\b", re.IGNORECASE),
    re.compile(r"\bgroup\s+addr(?:ess)?\s*[=:]?\s*(?P<tgid>\d{2,7})\b", re.IGNORECASE),
)

_FREQ_PATTERNS = (
    re.compile(r"\bvoice\s+freq(?:uency)?\s*[=:]?\s*(?P<freq>\d+(?:\.\d+)?)\b", re.IGNORECASE),
    re.compile(r"\bfreq(?:uency)?\s*[=:]\s*(?P<freq>\d+(?:\.\d+)?)\b", re.IGNORECASE),
    re.compile(r"\bfreq\((?P<freq>\d+(?:\.\d+)?)\)", re.IGNORECASE),
    re.compile(r"\bvc\s*[=:]?\s*(?P<freq>\d{6,12}(?:\.\d+)?)\b", re.IGNORECASE),
    re.compile(r"\bchannel\s*[=:]?\s*(?P<freq>\d{6,12}(?:\.\d+)?)\b", re.IGNORECASE),
)

_LABEL_PATTERNS = (
    re.compile(r"\blabel\s*[=:]\s*(?P<label>[^,;|]+)", re.IGNORECASE),
    re.compile(r"\btalk(?:\s*group|group)?\s+\d{2,7}\s+(?P<label>[A-Za-z][^,;|]+)", re.IGNORECASE),
)


def _normalise_frequency_hz(raw: str) -> int | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value < 10000:
        return int(round(value * 1_000_000))
    return int(round(value))


def _parse_tgid(text: str) -> int | None:
    for pattern in _TGID_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            tgid = int(match.group("tgid"))
        except (TypeError, ValueError):
            continue
        if 1 <= tgid <= 9_999_999:
            return tgid
    return None


def _parse_frequency(text: str) -> int | None:
    for pattern in _FREQ_PATTERNS:
        match = pattern.search(text)
        if match:
            return _normalise_frequency_hz(match.group("freq"))
    return None


def _parse_label(text: str) -> str:
    for pattern in _LABEL_PATTERNS:
        match = pattern.search(text)
        if match:
            label = " ".join(match.group("label").strip().strip('"\'').split())
            return label[:96]
    return ""


def _looks_like_config_line(lower: str) -> bool:
    if not any(token in lower for token in ("tgid", "talkgroup", "tg(", "whiteli", "blackli")):
        return False
    return any(token in lower for token in _CONFIG_LINE_TOKENS)


def parse_activity_line(line: str, labels: dict[int, str] | None = None) -> dict[str, Any] | None:
    text = str(line or "").strip()
    if not text:
        return None
    lower = text.lower()
    if _looks_like_config_line(lower):
        return None

    tgid = _parse_tgid(text)
    freq = _parse_frequency(text)
    label = _parse_label(text)
    if tgid is not None and labels:
        label = labels.get(tgid, label) or label

    phase = ""
    if any(token in lower for token in ("phase ii", "phase 2", "tdma", "p25p2", "ambe")):
        phase = "Phase II"
    elif any(token in lower for token in ("phase i", "phase 1", "fdma", "p25p1", "imbe")):
        phase = "Phase I"

    encrypted: bool | None = None
    muted: bool | None = None
    if any(token in lower for token in ("encrypted", "encryption", "cipher", "algid", "ciphertxt")):
        encrypted = True
        if any(token in lower for token in ("mute", "muted", "skip", "skipped", "nocrypt", "silence")):
            muted = True
    elif any(token in lower for token in ("plaintext", "plain text", " clear ", "clear voice")):
        encrypted = False
        muted = False

    voice_hint = any(token in lower for token in ("voice", "grant", "call", "imbe", "ambe", "vc", "new tgid"))
    if tgid is None and freq is None and encrypted is None and not phase:
        return None
    if tgid is None and not voice_hint:
        return None

    return {
        "ok": True,
        "source": "op25_log_text",
        "line": text[-500:],
        "parsed_utc": time.time(),
        "tgid": tgid,
        "talkgroup_label": label,
        "voice_frequency_hz": freq,
        "p25_phase": phase,
        "encrypted": encrypted,
        "muted": muted,
    }


def scan_activity_lines(lines: list[str] | tuple[str, ...], labels: dict[int, str] | None = None) -> dict[str, Any] | None:
    for line in reversed(list(lines or [])[-240:]):
        activity = parse_activity_line(line, labels)
        if activity and (activity.get("tgid") is not None or activity.get("voice_frequency_hz") is not None):
            return activity
    return None
