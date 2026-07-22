#!/usr/bin/env bash
set -u
python3 - "$@" <<'PY_METADATA_DISCOVERY_TOOL'
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

REPORT_DIR = Path(".p25_op25_metadata_discovery_reports")

TGID_RE = re.compile(r"\b(?:tgid|talkgroup)\s*(?:[:=]?\s+|[:=])(?P<tgid>\d+)\b", re.IGNORECASE)
FREQ_RE = re.compile(r"\b(?:freq|frequency|channel)\s*(?:[:=]?\s+|[:=]|\()(?P<freq>\d+(?:\.\d+)?)\b", re.IGNORECASE)


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def status_get(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{url.rstrip('/')}/api/status", timeout=8) as response:
        data = response.read().decode("utf-8")
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise RuntimeError("status response was not a JSON object")
    return payload


def scanner_post(url: str, action: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{url.rstrip('/')}/api/scanner/{action}",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        data = response.read().decode("utf-8")
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise RuntimeError(f"scanner {action} response was not a JSON object")
    return payload


def is_running(payload: dict[str, Any]) -> bool:
    process = payload.get("decoder_process")
    return isinstance(process, dict) and process.get("running") is True


def normalized_freq(raw: str) -> int | None:
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    if value < 10000:
        return int(round(value * 1_000_000))
    return int(round(value))


def mhz(freq_hz: int) -> str:
    return f"{freq_hz / 1_000_000:.6f} MHz"


def looks_configured_tgid(lower: str) -> bool:
    tokens = (
        "whitelist",
        "blacklist",
        "added talkgroup",
        "reading whitelist",
        "reading blacklist",
        "_whitelist.tsv",
        "_blacklist.tsv",
        "configured_tgid_ignored_for_activity",
    )
    return any(token in lower for token in tokens)


def sample_append(bucket: list[str], line: str, limit: int = 40) -> None:
    if line not in bucket:
        bucket.append(line)
        if len(bucket) > limit:
            del bucket[0 : len(bucket) - limit]


def classify_line(line: str, summary: dict[str, Any]) -> None:
    lower = line.lower()
    if not line.strip():
        return

    if looks_configured_tgid(lower):
        summary["configured_tgid_lines"] += 1
        sample_append(summary["configured_tgid_samples"], line)
        return

    if "imbe" in lower or "ambe" in lower:
        if "plaintext" in lower or "plain text" in lower or "clear" in lower:
            summary["plaintext_voice_frame_lines"] += 1
            sample_append(summary["plaintext_voice_frame_samples"], line)
        if "encrypted" in lower or re.search(r"\benc(?:rypted)?\b", lower):
            summary["encrypted_metadata_lines"] += 1
            sample_append(summary["encrypted_metadata_samples"], line)

    if "encrypted" in lower or "encryption" in lower or re.search(r"\benc(?:rypted)?\b", lower):
        if "plaintext" not in lower:
            summary["encrypted_metadata_lines"] += 1
            sample_append(summary["encrypted_metadata_samples"], line)

    tgid_match = TGID_RE.search(line)
    if tgid_match:
        tgid = int(tgid_match.group("tgid"))
        summary["active_tgid_candidate_lines"] += 1
        summary["active_tgid_candidates"][str(tgid)] += 1
        sample_append(summary["active_tgid_candidate_samples"], line)

    freq_match = FREQ_RE.search(line)
    if freq_match:
        freq = normalized_freq(freq_match.group("freq"))
    else:
        freq = None
    if freq is not None:
        if any(token in lower for token in ("voice", "grant", "call", "vc ", "voice channel")):
            summary["voice_frequency_candidate_lines"] += 1
            summary["voice_frequency_candidates_hz"][str(freq)] += 1
            sample_append(summary["voice_frequency_candidate_samples"], line)
        if any(token in lower for token in ("control", "control channel", "cc ", "cc:")):
            summary["control_channel_lines"] += 1
            summary["control_channel_candidates_hz"][str(freq)] += 1
            sample_append(summary["control_channel_samples"], line)


def empty_summary() -> dict[str, Any]:
    return {
        "snapshot_count": 0,
        "running_snapshots": 0,
        "states": Counter(),
        "status_active_control_hz": Counter(),
        "status_active_voice_hz": Counter(),
        "status_active_tgids": Counter(),
        "active_tgid_candidate_lines": 0,
        "active_tgid_candidates": Counter(),
        "active_tgid_candidate_samples": [],
        "voice_frequency_candidate_lines": 0,
        "voice_frequency_candidates_hz": Counter(),
        "voice_frequency_candidate_samples": [],
        "configured_tgid_lines": 0,
        "configured_tgid_samples": [],
        "plaintext_voice_frame_lines": 0,
        "plaintext_voice_frame_samples": [],
        "encrypted_metadata_lines": 0,
        "encrypted_metadata_samples": [],
        "control_channel_lines": 0,
        "control_channel_candidates_hz": Counter(),
        "control_channel_samples": [],
        "activity_numeric_max": {},
        "warnings": set(),
    }


def update_numeric_max(target: dict[str, int], key: str, value: Any) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        parsed = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        return
    target[key] = max(target.get(key, 0), parsed)


def analyze_snapshots(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    summary = empty_summary()
    for payload in snapshots:
        summary["snapshot_count"] += 1
        state = str(payload.get("scanner_state") or "unknown")
        summary["states"][state] += 1
        if is_running(payload):
            summary["running_snapshots"] += 1

        control = payload.get("active_control_frequency_hz")
        if isinstance(control, int):
            summary["status_active_control_hz"][str(control)] += 1
        voice = payload.get("active_voice_frequency_hz")
        if isinstance(voice, int):
            summary["status_active_voice_hz"][str(voice)] += 1
        tgid = payload.get("active_tgid")
        if isinstance(tgid, int):
            summary["status_active_tgids"][str(tgid)] += 1

        for warning in payload.get("warnings") or []:
            if warning:
                summary["warnings"].add(str(warning))

        activity = payload.get("activity_summary")
        if isinstance(activity, dict):
            for key, value in activity.items():
                if key in {"recent_events", "recent_activity", "recent_parsed_activity", "unique_tgids"}:
                    continue
                update_numeric_max(summary["activity_numeric_max"], key, value)

        for line in payload.get("log_tail") or []:
            classify_line(str(line), summary)

    serializable = dict(summary)
    for key in (
        "states",
        "status_active_control_hz",
        "status_active_voice_hz",
        "status_active_tgids",
        "active_tgid_candidates",
        "voice_frequency_candidates_hz",
        "control_channel_candidates_hz",
    ):
        serializable[key] = dict(sorted(summary[key].items()))
    serializable["warnings"] = sorted(summary["warnings"])
    return serializable


def report_text(summary: dict[str, Any], root: str) -> tuple[str, int, int, int]:
    passes = 0
    warns = 0
    fails = 0
    lines: list[str] = []
    lines.append("# PI-P25-SCANNER OP25 Metadata Discovery")
    lines.append("")
    lines.append(f"- Evidence source: `{root}`")
    lines.append(f"- Snapshot count: {summary['snapshot_count']}")
    lines.append(f"- Running snapshots: {summary['running_snapshots']}")
    lines.append("")

    def add(level: str, message: str) -> None:
        nonlocal passes, warns, fails
        lines.append(f"- {level}: {message}")
        if level == "PASS":
            passes += 1
        elif level == "WARN":
            warns += 1
        elif level == "FAIL":
            fails += 1

    if summary["snapshot_count"] > 0:
        add("PASS", "status snapshots captured")
    else:
        add("FAIL", "no status snapshots captured")
    if summary["running_snapshots"] > 0:
        add("PASS", "decoder running snapshots observed")
    else:
        add("WARN", "no running decoder snapshots observed")
    if summary["status_active_control_hz"]:
        freqs = ", ".join(mhz(int(freq)) for freq in summary["status_active_control_hz"].keys())
        add("PASS", f"status control-frequency fields observed: {freqs}")
    else:
        add("WARN", "no status control-frequency fields observed")
    if summary["status_active_voice_hz"] or summary["voice_frequency_candidates_hz"]:
        add("PASS", "voice-frequency metadata candidate observed")
    else:
        add("WARN", "no voice-frequency metadata candidate observed")
    if summary["status_active_tgids"] or summary["active_tgid_candidates"]:
        add("PASS", "active TGID metadata candidate observed")
    else:
        add("WARN", "no active TGID metadata candidate observed")
    if summary["configured_tgid_lines"] > 0:
        add("PASS", f"configured whitelist/blacklist TGID lines separated: {summary['configured_tgid_lines']}")
    else:
        add("WARN", "no configured whitelist/blacklist TGID lines observed")
    if summary["plaintext_voice_frame_lines"] > 0 or int(summary["activity_numeric_max"].get("clear_voice_events", 0) or 0) > 0:
        add("PASS", "plaintext/clear voice evidence observed")
    else:
        add("WARN", "no plaintext/clear voice evidence observed")
    if summary["encrypted_metadata_lines"] > 0 or int(summary["activity_numeric_max"].get("encrypted_events", 0) or 0) > 0:
        add("PASS", "encrypted-call metadata candidate observed")
    else:
        add("PASS", "no encrypted-call metadata observed in this window")

    lines.append("")
    lines.append("## Status Field Evidence")
    lines.append(f"- active_control_frequency_hz: {summary['status_active_control_hz'] or 'none'}")
    lines.append(f"- active_voice_frequency_hz: {summary['status_active_voice_hz'] or 'none'}")
    lines.append(f"- active_tgid: {summary['status_active_tgids'] or 'none'}")
    lines.append("")
    lines.append("## Activity Counters")
    if summary["activity_numeric_max"]:
        for key, value in sorted(summary["activity_numeric_max"].items()):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Candidate Line Counts")
    for key in (
        "active_tgid_candidate_lines",
        "voice_frequency_candidate_lines",
        "configured_tgid_lines",
        "plaintext_voice_frame_lines",
        "encrypted_metadata_lines",
        "control_channel_lines",
    ):
        lines.append(f"- {key}: {summary[key]}")
    lines.append("")
    lines.append("## Candidate Samples")
    sample_sections = (
        ("Active TGID candidates", "active_tgid_candidate_samples"),
        ("Voice-frequency candidates", "voice_frequency_candidate_samples"),
        ("Configured TGID lines", "configured_tgid_samples"),
        ("Plaintext voice frames", "plaintext_voice_frame_samples"),
        ("Encrypted metadata", "encrypted_metadata_samples"),
        ("Control-channel candidates", "control_channel_samples"),
    )
    for title, key in sample_sections:
        lines.append(f"### {title}")
        samples = summary.get(key) or []
        if samples:
            lines.append("```text")
            lines.extend(samples[-20:])
            lines.append("```")
        else:
            lines.append("- none")
        lines.append("")
    lines.append("## Warnings")
    for warning in summary.get("warnings") or ["none"]:
        lines.append(f"- {warning}")
    lines.append("")
    lines.append(f"SUMMARY: PASS={passes} WARN={warns} FAIL={fails}")
    lines.append("FINAL: PASS" if fails == 0 else "FINAL: FAIL")
    return "\n".join(lines) + "\n", passes, warns, fails


def run_self_test() -> int:
    fixture = [
        {
            "scanner_state": "running",
            "decoder_process": {"running": True},
            "active_control_frequency_hz": 852750000,
            "activity_summary": {"clear_voice_events": 2, "talkgroup_updates": 0},
            "log_tail": [
                "control channel frequency 852.750000",
                "added talkgroup 3105 from /tmp/TOPAZ_whitelist.tsv",
                "voice grant tgid 3105 frequency 853.275000 label Mesa Fire Dispatch clear",
                "07/05/26 13:46:12.803930 [0] IMBE (PLAINTEXT) 11 eb 7d errs 0",
                "tgid 4501 encrypted muted",
            ],
        }
    ]
    summary = analyze_snapshots(fixture)
    checks = [
        summary["snapshot_count"] == 1,
        summary["configured_tgid_lines"] == 1,
        summary["active_tgid_candidate_lines"] == 2,
        summary["voice_frequency_candidate_lines"] == 1,
        summary["plaintext_voice_frame_lines"] == 1,
        summary["encrypted_metadata_lines"] >= 1,
        "3105" in summary["active_tgid_candidates"],
    ]
    text, _passes, _warns, fails = report_text(summary, "self-test fixture")
    if not all(checks) or fails:
        print(text)
        print("FINAL: FAIL")
        return 1
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    report = REPORT_DIR / f"metadata_discovery_selftest_{stamp}.md"
    report.write_text(text, encoding="utf-8", newline="\n")
    print("PASS: metadata discovery self-test fixture passed")
    print(f"PASS: self-test report path: {report}")
    print("SUMMARY: PASS=2 WARN=0 FAIL=0")
    print("FINAL: PASS")
    return 0


def run_live(args: argparse.Namespace) -> int:
    if not args.yes:
        print("FAIL: live metadata discovery requires --yes")
        print("SUMMARY: PASS=0 WARN=0 FAIL=1")
        print("FINAL: FAIL")
        return 1
    if args.seconds <= 0 or args.interval <= 0:
        print("FAIL: --seconds and --interval must be positive integers")
        print("SUMMARY: PASS=0 WARN=0 FAIL=1")
        print("FINAL: FAIL")
        return 1

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    jsonl_path = REPORT_DIR / f"metadata_discovery_snapshots_{stamp}.jsonl"
    summary_path = REPORT_DIR / f"metadata_discovery_summary_{stamp}.json"
    report_path = REPORT_DIR / f"metadata_discovery_report_{stamp}.md"

    snapshots: list[dict[str, Any]] = []
    started = False
    try:
        initial = status_get(args.backend_url)
        snapshots.append(initial)
        if not is_running(initial) and not args.no_start:
            start_payload = scanner_post(args.backend_url, "start")
            snapshots.append(start_payload)
            started = True
        elif not is_running(initial) and args.no_start:
            print("WARN: scanner is not running and --no-start was requested")

        end_time = time.time() + args.seconds
        while time.time() < end_time:
            try:
                snapshots.append(status_get(args.backend_url))
            except Exception as exc:
                snapshots.append({"scanner_state": "status_sample_error", "warnings": [str(exc)]})
            time.sleep(args.interval)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"FAIL: live metadata discovery failed: {exc}")
        print("SUMMARY: PASS=0 WARN=0 FAIL=1")
        print("FINAL: FAIL")
        return 1
    finally:
        if started:
            try:
                snapshots.append(scanner_post(args.backend_url, "stop"))
            except Exception as exc:
                snapshots.append({"scanner_state": "scanner_stop_error", "warnings": [str(exc)]})

    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        for payload in snapshots:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")

    summary = analyze_snapshots(snapshots)
    text, passes, warns, fails = report_text(summary, str(jsonl_path))
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    report_path.write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    print(f"Report: {report_path}")
    print(f"Summary JSON: {summary_path}")
    print(f"Snapshot JSONL: {jsonl_path}")
    print(f"SUMMARY: PASS={passes} WARN={warns} FAIL={fails}")
    print("FINAL: PASS" if fails == 0 else "FINAL: FAIL")
    return 0 if fails == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover OP25 active metadata line formats from backend status/log tails")
    parser.add_argument("--self-test", action="store_true", help="run an isolated metadata classifier fixture")
    parser.add_argument("--seconds", type=int, default=240, help="live discovery duration in seconds")
    parser.add_argument("--interval", type=int, default=2, help="poll interval in seconds")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8070", help="backend base URL")
    parser.add_argument("--no-start", action="store_true", help="do not start scanner if stopped")
    parser.add_argument("--yes", action="store_true", help="required for live discovery")
    args = parser.parse_args()
    if args.self_test:
        return run_self_test()
    return run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
PY_METADATA_DISCOVERY_TOOL
