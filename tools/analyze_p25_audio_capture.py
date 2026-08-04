#!/usr/bin/env python3
"""Analyze a synchronized P25 pool/browser PCM diagnostic capture."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


LOG_TIMESTAMP = re.compile(r"^(\d\d/\d\d/\d\d \d\d:\d\d:\d\d\.\d+) (.*)$")
SYNC_TIME = re.compile(r"sync established, tuning time ([0-9.]+) seconds")
VOICE_UPDATE = re.compile(
    r"voice update:\s+tg\((\d+)\), rid\((\d+)\), freq\(([0-9.]+)\)"
)
RELEASE = re.compile(r"releasing:.*reason\(([^)]+)\)")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
        if isinstance(payload, dict):
            records.append(payload)
    return records


def gap_summary(timestamps: list[float]) -> dict[str, Any]:
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    return {
        "count": len(gaps),
        "mean_seconds": rounded(statistics.mean(gaps) if gaps else None),
        "p50_seconds": rounded(percentile(gaps, 0.50)),
        "p95_seconds": rounded(percentile(gaps, 0.95)),
        "p99_seconds": rounded(percentile(gaps, 0.99)),
        "max_seconds": rounded(max(gaps) if gaps else None),
        "over_30ms": sum(gap > 0.03 for gap in gaps),
        "over_50ms": sum(gap > 0.05 for gap in gaps),
        "over_100ms": sum(gap > 0.10 for gap in gaps),
        "over_500ms": sum(gap > 0.50 for gap in gaps),
    }


def load_capture_window(directory: Path) -> tuple[float | None, float | None]:
    path = directory / "browser_capture_manifest.json"
    if not path.exists():
        return None, None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return float(payload["started_utc"]), float(payload["ended_utc"])


def summarize_pool_segments(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe PCM between OP25 DRAIN/DROP call-boundary flags."""
    segments: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def finish(boundary: int | None) -> None:
        if not current:
            return
        first = float(current[0]["utc"])
        last = float(current[-1]["utc"])
        frame_count = len(current)
        media_seconds = frame_count * 0.02
        wall_seconds = max(0.02, last - first + 0.02)
        segments.append(
            {
                "start_offset_seconds": current[0].get("offset_seconds"),
                "end_offset_seconds": current[-1].get("offset_seconds"),
                "frames": frame_count,
                "active_frames": sum(int(event.get("rms") or 0) >= 25 for event in current),
                "forwarded_frames": sum(bool(event.get("forwarded")) for event in current),
                "media_seconds": round(media_seconds, 3),
                "input_wall_seconds": round(wall_seconds, 3),
                "missing_media_seconds": round(max(0.0, wall_seconds - media_seconds), 3),
                "largest_input_gap_seconds": round(
                    max(
                        (
                            float(right["utc"]) - float(left["utc"])
                            for left, right in zip(current, current[1:])
                        ),
                        default=0.0,
                    ),
                    6,
                ),
                "boundary_flag": boundary,
            }
        )
        current.clear()

    for event in events:
        if event.get("kind") == "audio":
            current.append(event)
        elif event.get("kind") == "flag":
            raw_flag = event.get("flag")
            finish(int(raw_flag) if raw_flag is not None else None)
    finish(None)
    return segments


def analyze_op25_log(
    path: Path,
    *,
    started_utc: float | None,
    ended_utc: float | None,
    timezone_name: str,
) -> dict[str, Any]:
    if not path.exists():
        return {"available": False}
    timezone = ZoneInfo(timezone_name)
    sync_times: list[float] = []
    releases: Counter[str] = Counter()
    talkgroups: Counter[int] = Counter()
    frequencies: Counter[str] = Counter()
    timeout_count = 0
    selected_lines = 0
    timeline: list[dict[str, Any]] = []

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = LOG_TIMESTAMP.match(line)
        if not match:
            continue
        local_time = dt.datetime.strptime(match.group(1), "%m/%d/%y %H:%M:%S.%f").replace(
            tzinfo=timezone
        )
        timestamp = local_time.timestamp()
        if started_utc is not None and timestamp < started_utc:
            continue
        if ended_utc is not None and timestamp > ended_utc:
            continue
        selected_lines += 1
        message = match.group(2)
        event: dict[str, Any] | None = None
        sync = SYNC_TIME.search(message)
        voice = VOICE_UPDATE.search(message)
        release = RELEASE.search(message)
        if sync:
            value = float(sync.group(1))
            sync_times.append(value)
            event = {"kind": "sync", "tuning_seconds": value}
        elif "voice channel timeout" in message:
            timeout_count += 1
            event = {"kind": "voice_timeout"}
        elif release:
            reason = release.group(1)
            releases[reason] += 1
            event = {"kind": "release", "reason": reason}
        elif voice:
            tgid = int(voice.group(1))
            talkgroups[tgid] += 1
            frequencies[voice.group(3)] += 1
            event = {
                "kind": "voice_update",
                "tgid": tgid,
                "rid": int(voice.group(2)),
                "frequency_mhz": float(voice.group(3)),
            }
        if event is not None:
            event["offset_seconds"] = (
                round(timestamp - started_utc, 6) if started_utc is not None else None
            )
            timeline.append(event)

    return {
        "available": True,
        "timezone": timezone_name,
        "log_lines_in_capture_window": selected_lines,
        "voice_updates": sum(talkgroups.values()),
        "voice_updates_by_tgid": dict(sorted(talkgroups.items())),
        "voice_frequencies_mhz": dict(sorted(frequencies.items())),
        "sync_events": len(sync_times),
        "sync_tuning_seconds": {
            "mean": rounded(statistics.mean(sync_times) if sync_times else None),
            "p50": rounded(percentile(sync_times, 0.50)),
            "p95": rounded(percentile(sync_times, 0.95)),
            "max": rounded(max(sync_times) if sync_times else None),
            "over_500ms": sum(value > 0.5 for value in sync_times),
            "over_1s": sum(value > 1.0 for value in sync_times),
        },
        "voice_channel_timeouts": timeout_count,
        "releases_by_reason": dict(sorted(releases.items())),
        "timeline": timeline,
    }


def analyze(directory: Path, timezone_name: str = "America/Phoenix") -> dict[str, Any]:
    events = load_jsonl(directory / "pool_events.jsonl")
    browser = load_jsonl(directory / "browser_frames.jsonl")
    started_utc, ended_utc = load_capture_window(directory)
    audio = [event for event in events if event.get("kind") == "audio"]
    flags = [event for event in events if event.get("kind") == "flag"]
    forwarded = [event for event in audio if event.get("forwarded")]
    active = [event for event in audio if int(event.get("rms") or 0) >= 25]
    dropped_active = [event for event in active if not event.get("forwarded")]
    input_times = [float(event["utc"]) for event in audio]
    forward_times = [float(event["utc"]) for event in forwarded]
    browser_times = [float(event["utc"]) for event in browser]
    flag_counts = Counter(
        int(event["flag"]) if event.get("flag") is not None else -1 for event in flags
    )
    segments = summarize_pool_segments(events)

    longest_input_gaps = []
    for previous, current in zip(audio, audio[1:]):
        gap = float(current["utc"]) - float(previous["utc"])
        if gap > 0.03:
            longest_input_gaps.append(
                {
                    "seconds": round(gap, 6),
                    "after_offset": previous.get("offset_seconds"),
                    "before_rms": previous.get("rms"),
                    "after_rms": current.get("rms"),
                }
            )
    longest_input_gaps.sort(key=lambda item: item["seconds"], reverse=True)

    browser_p25 = [event for event in browser if event.get("source") == "P25"]
    browser_p25_silent = [event for event in browser_p25 if int(event.get("rms") or 0) <= 1]

    return {
        "capture_directory": str(directory),
        "pool": {
            "event_count": len(events),
            "audio_frames": len(audio),
            "active_frames": len(active),
            "forwarded_frames": len(forwarded),
            "active_frames_not_forwarded": len(dropped_active),
            "flag_packets": len(flags),
            "drain_flags": flag_counts.get(0, 0),
            "drop_flags": flag_counts.get(1, 0),
            "segments": segments,
            "segment_totals": {
                "segments_with_audio": len(segments),
                "media_seconds": round(sum(item["media_seconds"] for item in segments), 3),
                "missing_media_seconds": round(
                    sum(item["missing_media_seconds"] for item in segments), 3
                ),
                "segments_with_gap_over_100ms": sum(
                    item["largest_input_gap_seconds"] > 0.1 for item in segments
                ),
                "segments_with_gap_over_500ms": sum(
                    item["largest_input_gap_seconds"] > 0.5 for item in segments
                ),
            },
            "input_cadence": gap_summary(input_times),
            "forwarded_cadence": gap_summary(forward_times),
            "longest_input_gaps": longest_input_gaps[:25],
        },
        "browser": {
            "frames": len(browser),
            "cadence": gap_summary(browser_times),
            "p25_annotated_frames": len(browser_p25),
            "p25_silent_frames": len(browser_p25_silent),
            "p25_non_silent_frames": len(browser_p25) - len(browser_p25_silent),
        },
        "op25_decoder": analyze_op25_log(
            directory / "op25-runtime.log",
            started_utc=started_utc,
            ended_utc=ended_utc,
            timezone_name=timezone_name,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--timezone",
        default="America/Phoenix",
        help="timezone used by OP25 log timestamps (Pi default: America/Phoenix)",
    )
    args = parser.parse_args()
    report = analyze(args.capture_dir, timezone_name=args.timezone)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
