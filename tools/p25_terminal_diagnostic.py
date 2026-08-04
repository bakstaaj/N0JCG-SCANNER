#!/usr/bin/env python3
"""Capture OP25 terminal receiver state and frequency-error measurements."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


def post_command(
    url: str,
    command: str,
    channel: int = 0,
    arg1: int = 0,
) -> list[dict[str, Any]]:
    payload = [{"command": command, "arg1": arg1, "arg2": channel}]
    request = Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=3) as response:
        result = json.load(response)
    return result if isinstance(result, list) else []


def channel_update(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for message in messages:
        if message.get("json_type") == "channel_update":
            return message
    return {}


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    receivers: dict[str, dict[str, Any]] = {}
    for record in records:
        for channel, state in (record.get("channels") or {}).items():
            if not isinstance(state, dict):
                continue
            summary = receivers.setdefault(
                channel,
                {
                    "name": state.get("name", ""),
                    "samples": 0,
                    "errors_hz": [],
                    "frequencies_hz": Counter(),
                    "tags": Counter(),
                    "talkgroups": Counter(),
                },
            )
            summary["samples"] += 1
            error = state.get("error")
            if isinstance(error, (int, float)):
                summary["errors_hz"].append(float(error))
            frequency = state.get("freq")
            if isinstance(frequency, (int, float)):
                summary["frequencies_hz"][str(int(frequency))] += 1
            summary["tags"][str(state.get("tag") or "")] += 1
            tgid = state.get("tgid")
            if isinstance(tgid, int):
                summary["talkgroups"][str(tgid)] += 1

    rendered: dict[str, Any] = {}
    for channel, summary in receivers.items():
        errors = summary.pop("errors_hz")
        summary["frequency_error_hz"] = {
            "samples": len(errors),
            "mean": rounded(statistics.mean(errors) if errors else None),
            "p50": rounded(percentile(errors, 0.50)),
            "p95_absolute": rounded(percentile([abs(value) for value in errors], 0.95)),
            "minimum": rounded(min(errors) if errors else None),
            "maximum": rounded(max(errors) if errors else None),
        }
        summary["frequencies_hz"] = dict(summary["frequencies_hz"])
        summary["tags"] = dict(summary["tags"])
        summary["talkgroups"] = dict(summary["talkgroups"])
        rendered[channel] = summary
    return {"record_count": len(records), "receivers": rendered}


def capture(url: str, duration_seconds: float, interval_seconds: float) -> tuple[list[dict], dict]:
    records: list[dict[str, Any]] = []
    started = time.time()
    deadline = started + duration_seconds
    errors: Counter[str] = Counter()
    while time.time() < deadline:
        sample_started = time.time()
        try:
            update = channel_update(post_command(url, "update"))
            channels = {
                key: value
                for key, value in update.items()
                if str(key).isdigit() and isinstance(value, dict)
            }
            records.append(
                {
                    "utc": sample_started,
                    "offset_seconds": round(sample_started - started, 6),
                    "channels": channels,
                }
            )
        except Exception as exc:  # diagnostic must preserve partial data
            errors[f"{type(exc).__name__}: {exc}"] += 1
        time.sleep(max(0.0, interval_seconds - (time.time() - sample_started)))
    summary = summarize(records)
    summary.update(
        {
            "started_utc": started,
            "ended_utc": time.time(),
            "duration_seconds": duration_seconds,
            "interval_seconds": interval_seconds,
            "request_errors": dict(errors),
        }
    )
    return records, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18091/")
    parser.add_argument("--duration-seconds", type=float, default=300.0)
    parser.add_argument("--interval-seconds", type=float, default=0.2)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    records, summary = capture(args.url, args.duration_seconds, args.interval_seconds)
    (args.output_dir / "terminal_receiver_samples.jsonl").write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )
    (args.output_dir / "terminal_receiver_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
