"""Best-effort OP25 runtime status parsing.

The parser is intentionally conservative. It extracts only operational status
from log text that the backend already receives from OP25. It does not control
OP25 and does not interpret encrypted audio beyond reporting encrypted/muted
state when OP25 logs indicate it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_TGID_PATTERNS = [
    re.compile(r"\btgid\s*[:=]\s*(?P<tgid>\d+)\b", re.IGNORECASE),
    re.compile(r"\btgid\s+(?P<tgid>\d+)\b", re.IGNORECASE),
    re.compile(r"\btg(?:id)?\((?P<tgid>\d+)\)", re.IGNORECASE),
    re.compile(r"\btalkgroup\s+(?P<tgid>\d+)\b", re.IGNORECASE),
]

_FREQ_PATTERNS = [
    re.compile(r"\bcontrol\s+channel\s*(?:[:=]\s*|\s+)(?P<freq>\d+(?:\.\d+)?)\b", re.IGNORECASE),
    re.compile(r"\bfreq(?:uency)?\s*(?:[:=]\s*|\s+)(?P<freq>\d+(?:\.\d+)?)\b", re.IGNORECASE),
    re.compile(r"\bfreq\((?P<freq>\d+(?:\.\d+)?)\)", re.IGNORECASE),
    re.compile(r"\bchannel\s*(?:[:=]\s*|\s+)(?P<freq>\d{6,12}(?:\.\d+)?)\b", re.IGNORECASE),
]

_LABEL_PATTERNS = [
    re.compile(r"\blabel\s*(?:[:=]\s*|\s+)(?P<label>[^,;]+)", re.IGNORECASE),
    re.compile(r"\btalkgroup\s+\d+\s+(?P<label>[A-Za-z][^,;]+)", re.IGNORECASE),
]


@dataclass(slots=True)
class RuntimeStatusUpdate:
    """Parsed runtime status from one OP25/backend log line."""

    line: str
    control_frequency_hz: int | None = None
    voice_frequency_hz: int | None = None
    tgid: int | None = None
    talkgroup_label: str = ""
    p25_phase: str = ""
    encrypted: bool | None = None
    muted: bool | None = None
    control_channel_state: str = ""
    parser_notes: list[str] = field(default_factory=list)

    @property
    def has_update(self) -> bool:
        return any(
            [
                self.control_frequency_hz is not None,
                self.voice_frequency_hz is not None,
                self.tgid is not None,
                self.talkgroup_label,
                self.p25_phase,
                self.encrypted is not None,
                self.muted is not None,
                self.control_channel_state,
                self.parser_notes,
            ]
        )

    def to_status_dict(self) -> dict[str, object]:
        return {
            "last_parsed_line": self.line,
            "control_frequency_hz": self.control_frequency_hz,
            "voice_frequency_hz": self.voice_frequency_hz,
            "tgid": self.tgid,
            "talkgroup_label": self.talkgroup_label,
            "p25_phase": self.p25_phase,
            "encrypted": self.encrypted,
            "muted": self.muted,
            "control_channel_state": self.control_channel_state,
            "parser_notes": list(self.parser_notes),
        }


class RuntimeStatusParser:
    """Parse common OP25 status/log lines into UI-friendly fields."""

    def parse_line(self, line: str) -> RuntimeStatusUpdate:
        update = RuntimeStatusUpdate(line=line.strip())
        text = update.line
        lower = text.lower()

        if not text:
            return update

        if "control channel timeout" in lower:
            update.control_channel_state = "searching"
            update.parser_notes.append("control_channel_timeout")
        elif "set control channel" in lower:
            update.control_channel_state = "searching"
            update.parser_notes.append("control_channel_hunt")
        elif any(token in lower for token in (
            "tsbk", "network status broadcast", "rfss status broadcast",
            "identifier update", "voice update", "voice grant",
            "reconfiguring nac",
        )):
            update.control_channel_state = "locked"
            update.parser_notes.append("control_channel_activity")

        parsed_tgid = self._parse_tgid(text)
        if parsed_tgid is not None and self._looks_like_configured_talkgroup(lower):
            update.parser_notes.append("configured_tgid_ignored_for_activity")
            return update
        update.tgid = parsed_tgid
        freq = self._parse_frequency(text)
        if freq is not None:
            if self._looks_like_control_channel(lower):
                update.control_frequency_hz = freq
                update.parser_notes.append("control_frequency")
            elif self._looks_like_voice_channel(lower) or update.tgid is not None:
                update.voice_frequency_hz = freq
                update.parser_notes.append("voice_frequency")
            else:
                update.control_frequency_hz = freq
                update.parser_notes.append("frequency_defaulted_to_control")

        label = self._parse_label(text)
        if label:
            update.talkgroup_label = label

        if "phase ii" in lower or "phase 2" in lower or "tdma" in lower or "p25p2" in lower:
            update.p25_phase = "Phase II"
        elif "phase i" in lower or "phase 1" in lower or "fdma" in lower:
            update.p25_phase = "Phase I"

        voice_frame_seen = "imbe" in lower or "ambe" in lower
        encrypted_or_blocked_seen = self._looks_like_encrypted_or_blocked(lower)
        if voice_frame_seen:
            if "imbe" in lower and not update.p25_phase:
                update.p25_phase = "Phase I"
            elif "ambe" in lower and not update.p25_phase:
                update.p25_phase = "Phase II"
            if "plaintext" in lower or "plain text" in lower or "clear" in lower:
                update.encrypted = False
                update.muted = False
                update.parser_notes.append("clear_voice_frame")
            elif encrypted_or_blocked_seen:
                update.encrypted = True
                update.muted = True
                update.parser_notes.append("encrypted_or_blocked_voice_frame")

        if encrypted_or_blocked_seen:
            if "clear" in lower and "not encrypted" in lower:
                update.encrypted = False
            else:
                update.encrypted = True
                update.parser_notes.append("encrypted")
                if any(token in lower for token in ("mute", "muted", "silence", "skip", "skipped", "nocrypt")):
                    update.muted = True
        elif " clear " in f" {lower} " and ("voice" in lower or update.tgid is not None):
            update.encrypted = False
            update.muted = False

        if any(token in lower for token in ("muted", "silenced", "skipped")) and update.muted is None:
            update.muted = True

        return update

    # ACTIVE_AUDIO_ONLY_ENCRYPTED_DETECT_V0_4H4
    @staticmethod
    def _looks_like_encrypted_or_blocked(lower: str) -> bool:
        """Return True for OP25 lines that indicate blocked/encrypted audio.

        OP25 sometimes logs encrypted calls as CIPHERTXT, algid, crypt,
        nocrypt, skipped, or muted without including the literal word
        encrypted on the same line. Treat those as display-suppression
        signals so blocked encrypted calls do not become active-audio UI
        entries.
        """

        if "not encrypted" in lower or "plaintext" in lower or "plain text" in lower:
            return False
        tokens = (
            "encrypted",
            "encryption",
            "ciphertxt",
            "ciphertext",
            "p25_crypt",
            "crypt_algs",
            "nocrypt",
            "algid",
            "muted encrypted",
            "encrypted skip",
            "skipped encrypted",
        )
        if any(token in lower for token in tokens):
            return True
        return re.search(r"\benc(?:rypted)?\b", lower) is not None

    @staticmethod
    def _parse_tgid(text: str) -> int | None:
        for pattern in _TGID_PATTERNS:
            match = pattern.search(text)
            if match:
                try:
                    return int(match.group("tgid"))
                except ValueError:
                    return None
        return None

    @staticmethod
    def _parse_frequency(text: str) -> int | None:
        for pattern in _FREQ_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            raw = match.group("freq")
            try:
                value = float(raw)
            except ValueError:
                continue
            if value <= 0:
                continue
            if value < 10000:
                return int(round(value * 1_000_000))
            return int(round(value))
        return None

    @staticmethod
    def _parse_label(text: str) -> str:
        for pattern in _LABEL_PATTERNS:
            match = pattern.search(text)
            if match:
                label = match.group("label").strip().strip('"').strip("'")
                return label[:80]
        return ""

    @staticmethod
    def _looks_like_configured_talkgroup(lower: str) -> bool:
        if not any(token in lower for token in ("tgid", "tg(", "talkgroup")):
            return False
        config_tokens = (
            "whitelist",
            "blacklist",
            "whiteli",
            "blackli",
            "_whitelist",
            "_blacklist",
            "_whiteli",
            "_blackli",
            ".tsv",
            " from /",
            " from runtime/",
            "added talkgroup",
            "adding talkgroup",
            "loaded talkgroup",
            "loading talkgroup",
            "reading talkgroup",
            "configured talkgroup",
        )
        return any(token in lower for token in config_tokens)

    @staticmethod
    def _looks_like_control_channel(lower: str) -> bool:
        return any(token in lower for token in ("control", "ctrl", "cc ", "cc:", "control channel"))

    @staticmethod
    def _looks_like_voice_channel(lower: str) -> bool:
        return any(token in lower for token in ("voice", "grant", "call", "vc ", "voice channel"))
