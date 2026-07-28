"""In-memory runtime activity counters for parsed OP25 status lines.

The activity tracker only summarizes status fields already parsed from OP25 log
text. It does not control OP25, persist sensitive data, or attempt to decode
or decrypt encrypted audio.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    from pi_p25_scanner.runtime_status import RuntimeStatusUpdate
else:
    from .runtime_status import RuntimeStatusUpdate


RECENT_EVENT_LIMIT = 25
UNIQUE_TGID_LIMIT = 1024
VOICE_CALL_DEDUP_SECONDS = 2.5


@dataclass
class RuntimeActivityTracker:
    """Track lightweight activity counters for the current backend process."""

    state_path: Path | None = None
    started_utc: float = field(default_factory=time.time)
    updated_utc: float = field(default_factory=time.time)
    parsed_status_lines: int = 0
    control_frequency_updates: int = 0
    voice_frequency_updates: int = 0
    talkgroup_updates: int = 0
    voice_call_events: int = 0
    distinct_voice_calls: int = 0
    encrypted_events: int = 0
    muted_events: int = 0
    clear_voice_events: int = 0
    unique_tgids: set[int] = field(default_factory=set)
    unique_tgid_order: deque[int] = field(
        default_factory=lambda: deque(maxlen=UNIQUE_TGID_LIMIT)
    )
    recent_events: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=RECENT_EVENT_LIMIT)
    )
    _last_voice_call_signature: tuple[int | None, int | None] | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _last_voice_call_utc: float = field(
        default=0.0,
        init=False,
        repr=False,
        compare=False,
    )
    _lock: threading.RLock = field(
        default_factory=threading.RLock,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.state_path is None:
            return
        self.state_path = Path(self.state_path)
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            saved_calls = int(payload.get("distinct_voice_calls", 0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        self.distinct_voice_calls = max(0, saved_calls)

    def _persist_distinct_voice_calls_unlocked(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_name(
                f".{self.state_path.name}.{os.getpid()}.tmp"
            )
            temporary.write_text(
                json.dumps(
                    {
                        "distinct_voice_calls": self.distinct_voice_calls,
                        "updated_utc": self.updated_utc,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.state_path)
        except OSError:
            # Counter persistence must never interrupt live scanner processing.
            return

    def reset(
        self,
        *,
        preserve_distinct_voice_calls: bool = False,
    ) -> dict[str, Any]:
        """Reset counters and return a fresh snapshot."""

        with self._lock:
            self.started_utc = time.time()
            self.updated_utc = self.started_utc
            self.parsed_status_lines = 0
            self.control_frequency_updates = 0
            self.voice_frequency_updates = 0
            self.talkgroup_updates = 0
            self.voice_call_events = 0
            if not preserve_distinct_voice_calls:
                self.distinct_voice_calls = 0
            self.encrypted_events = 0
            self.muted_events = 0
            self.clear_voice_events = 0
            self._last_voice_call_signature = None
            self._last_voice_call_utc = 0.0
            self.unique_tgids.clear()
            self.unique_tgid_order.clear()
            self.recent_events.clear()
            self._persist_distinct_voice_calls_unlocked()
            return self._snapshot_unlocked()

    def _record_unique_tgid(self, tgid: int) -> None:
        if tgid in self.unique_tgids:
            return

        if len(self.unique_tgid_order) >= UNIQUE_TGID_LIMIT:
            oldest = self.unique_tgid_order.popleft()
            self.unique_tgids.discard(oldest)

        self.unique_tgid_order.append(tgid)
        self.unique_tgids.add(tgid)

    def record(self, update: RuntimeStatusUpdate) -> dict[str, Any]:
        """Record one parsed runtime status update and return a snapshot."""

        with self._lock:
            if not update.has_update:
                return self._snapshot_unlocked()

            self.updated_utc = time.time()
            self.parsed_status_lines += 1

            event: dict[str, Any] = {
                "updated_utc": self.updated_utc,
                "line": update.line,
                "control_frequency_hz": update.control_frequency_hz,
                "voice_frequency_hz": update.voice_frequency_hz,
                "tgid": update.tgid,
                "talkgroup_label": update.talkgroup_label,
                "p25_phase": update.p25_phase,
                "voice_call": update.voice_call,
                "encrypted": update.encrypted,
                "muted": update.muted,
                "parser_notes": list(update.parser_notes),
            }

            if update.control_frequency_hz is not None:
                self.control_frequency_updates += 1
            if update.voice_frequency_hz is not None:
                self.voice_frequency_updates += 1
            if update.tgid is not None:
                self.talkgroup_updates += 1
                self._record_unique_tgid(update.tgid)
            if update.voice_call:
                self.voice_call_events += 1
                signature = (update.tgid, update.voice_frequency_hz)
                if self._last_voice_call_signature is not None:
                    previous_tgid, previous_frequency = self._last_voice_call_signature
                    signature = (
                        update.tgid if update.tgid is not None else previous_tgid,
                        update.voice_frequency_hz
                        if update.voice_frequency_hz is not None
                        else previous_frequency,
                    )

                if (
                    signature != self._last_voice_call_signature
                    or self.updated_utc - self._last_voice_call_utc
                    > VOICE_CALL_DEDUP_SECONDS
                ):
                    self.distinct_voice_calls += 1
                    self._persist_distinct_voice_calls_unlocked()

                self._last_voice_call_signature = signature
                self._last_voice_call_utc = self.updated_utc
            if update.encrypted is True:
                self.encrypted_events += 1
            if update.encrypted is False:
                self.clear_voice_events += 1
            if update.muted is True:
                self.muted_events += 1

            self.recent_events.append(event)
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "started_utc": self.started_utc,
            "updated_utc": self.updated_utc,
            "parsed_status_lines": self.parsed_status_lines,
            "control_frequency_updates": self.control_frequency_updates,
            "voice_frequency_updates": self.voice_frequency_updates,
            "talkgroup_updates": self.talkgroup_updates,
            "voice_call_events": self.voice_call_events,
            "distinct_voice_calls": self.distinct_voice_calls,
            "encrypted_events": self.encrypted_events,
            "muted_events": self.muted_events,
            "clear_voice_events": self.clear_voice_events,
            "unique_tgid_count": len(self.unique_tgids),
            "unique_tgids": sorted(self.unique_tgids),
            "recent_events": list(self.recent_events),
        }

    def snapshot(self) -> dict[str, Any]:
        """Return a consistent JSON-friendly activity summary."""

        with self._lock:
            return self._snapshot_unlocked()
