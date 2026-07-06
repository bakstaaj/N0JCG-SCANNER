#!/usr/bin/env python3
"""Watch OP25 log output and mute the PI-P25 browser audio bridge on encrypted indicators."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ENCRYPTED_PATTERNS = [
    re.compile(r"\bCIPHERTXT\b", re.IGNORECASE),
    re.compile(r"p25_crypt_algs", re.IGNORECASE),
    re.compile(r"skip encrypted call", re.IGNORECASE),
    re.compile(r"encrypted skip", re.IGNORECASE),
    re.compile(r"algorithm module not found", re.IGNORECASE),
    re.compile(r"\balgid\s*=\s*(?:0x)?[0-9a-f]+", re.IGNORECASE),
]


@dataclass
class WatchStats:
    started_utc: float = field(default_factory=time.time)
    lines_read: int = 0
    encrypted_matches: int = 0
    gate_requests: int = 0
    gate_errors: int = 0
    last_match_line: str | None = None
    last_match_utc: float | None = None
    last_gate_response: dict[str, object] | None = None

    def snapshot(self) -> dict[str, object]:
        now = time.time()
        return {
            "ok": True,
            "mode": "op25-encrypted-log-gate-watcher-v0.3k",
            "uptime_seconds": round(now - self.started_utc, 3),
            "lines_read": self.lines_read,
            "encrypted_matches": self.encrypted_matches,
            "gate_requests": self.gate_requests,
            "gate_errors": self.gate_errors,
            "last_match_age_seconds": None if self.last_match_utc is None else round(now - self.last_match_utc, 3),
            "last_match_line": self.last_match_line,
            "last_gate_response": self.last_gate_response,
        }


def is_encrypted_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in ENCRYPTED_PATTERNS)


def wait_for_log(path: Path, deadline: float) -> bool:
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.1)
    return path.exists()


def request_gate(bridge_url: str, hold_ms: int, reason: str, timeout: float) -> dict[str, object]:
    query = urllib.parse.urlencode({"hold_ms": str(hold_ms), "reason": reason})
    url = bridge_url.rstrip("/") + "/api/audio/gate?" + query
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch OP25 log and gate encrypted audio bursts")
    parser.add_argument("--op25-log", required=True)
    parser.add_argument("--bridge-url", default="http://127.0.0.1:8072")
    parser.add_argument("--hold-ms", type=int, default=5000)
    parser.add_argument("--duration", type=int, default=120)
    parser.add_argument("--request-timeout", type=float, default=1.0)
    parser.add_argument("--rate-limit-ms", type=int, default=250)
    parser.add_argument("--summary-file", default="")
    args = parser.parse_args()

    stats = WatchStats()
    log_path = Path(args.op25_log)
    deadline = time.time() + max(1, args.duration) + 5
    if not wait_for_log(log_path, time.time() + 10):
        print(f"WARN: OP25 log did not appear: {log_path}", flush=True)
        if args.summary_file:
            Path(args.summary_file).write_text(json.dumps(stats.snapshot(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0

    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        last_gate_utc = 0.0
        while time.time() < deadline:
            line = handle.readline()
            if not line:
                time.sleep(0.05)
                continue
            stats.lines_read += 1
            clean = line.strip()
            if not clean or not is_encrypted_line(clean):
                continue
            stats.encrypted_matches += 1
            stats.last_match_line = clean[:240]
            stats.last_match_utc = time.time()
            now = time.time()
            if (now - last_gate_utc) * 1000.0 < max(0, args.rate_limit_ms):
                continue
            last_gate_utc = now
            try:
                response = request_gate(args.bridge_url, max(0, args.hold_ms), "op25-encrypted-log", args.request_timeout)
                stats.gate_requests += 1
                stats.last_gate_response = response
                print("GATE", json.dumps({"line": clean[:160], "response": response}, sort_keys=True), flush=True)
            except Exception as exc:  # noqa: BLE001
                stats.gate_errors += 1
                print(f"GATE_ERROR {exc}", flush=True)

    summary = stats.snapshot()
    print("FINAL_GATE_WATCHER_STATUS", json.dumps(summary, sort_keys=True), flush=True)
    if args.summary_file:
        Path(args.summary_file).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
