#!/usr/bin/env bash
set -u

python3 - "$@" <<'PY_ANALYZE_EVIDENCE'
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

FAILURE_STATES = {
    "decoder_command_invalid",
    "decoder_start_failed",
    "decoder_missing",
    "config_error",
}

REPORT_DIR = Path(".p25_live_evidence_analyze_reports")


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(float(stripped))
        except ValueError:
            return None
    return None


def find_latest_evidence_root() -> Path | None:
    candidates: list[Path] = []
    for base in (Path("runtime/evidence"), Path(".p25_live_activity_capture_reports")):
        if not base.exists():
            continue
        candidates.append(base)
        for child in base.iterdir():
            if child.is_dir() or child.suffix.lower() == ".json":
                candidates.append(child)
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def collect_json_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() == ".json" else []
    if not path.exists():
        return []
    return sorted(path.rglob("*.json"))


def iter_status_records(payload: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if "snapshots" in payload and isinstance(payload["snapshots"], list):
            for item in payload["snapshots"]:
                records.extend(iter_status_records(item))
            return records
        if "status" in payload and isinstance(payload["status"], dict):
            records.append(payload["status"])
            return records
        if "payload" in payload and isinstance(payload["payload"], dict):
            records.extend(iter_status_records(payload["payload"]))
            if records:
                return records
        if "scanner_state" in payload or "decoder_process" in payload or "activity_summary" in payload:
            records.append(payload)
            return records
    if isinstance(payload, list):
        for item in payload:
            records.extend(iter_status_records(item))
    return records


def boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
    return None


def summarize(records: list[dict[str, Any]], files: list[Path]) -> dict[str, Any]:
    states: Counter[str] = Counter()
    control_freqs: Counter[int] = Counter()
    voice_freqs: Counter[int] = Counter()
    tgids: dict[int, str] = {}
    phase_counts: Counter[str] = Counter()
    warnings: set[str] = set()
    last_events: list[str] = []
    log_lines: list[str] = []
    activity_numeric_max: dict[str, int] = {}
    activity_unique_tgids: set[int] = set()
    running_snapshots = 0
    encrypted_snapshots = 0
    muted_snapshots = 0
    clear_voice_snapshots = 0
    failure_snapshots = 0

    for record in records:
        state = str(record.get("scanner_state") or "unknown")
        states[state] += 1
        if state in FAILURE_STATES:
            failure_snapshots += 1

        process = record.get("decoder_process") if isinstance(record.get("decoder_process"), dict) else {}
        if boolish(process.get("running")) is True:
            running_snapshots += 1

        control = as_int(record.get("active_control_frequency_hz"))
        if control:
            control_freqs[control] += 1

        voice = as_int(record.get("active_voice_frequency_hz"))
        if voice:
            voice_freqs[voice] += 1

        tgid = as_int(record.get("active_tgid"))
        label = str(record.get("active_talkgroup_label") or "").strip()
        if tgid:
            tgids[tgid] = label or tgids.get(tgid, "")

        phase = str(record.get("p25_phase") or "").strip()
        if phase and phase.lower() != "unknown":
            phase_counts[phase] += 1

        encrypted = boolish(record.get("encrypted"))
        muted = boolish(record.get("muted"))
        if encrypted is True:
            encrypted_snapshots += 1
        if muted is True:
            muted_snapshots += 1
        if encrypted is False and voice:
            clear_voice_snapshots += 1

        for warning in record.get("warnings") or []:
            if warning:
                warnings.add(str(warning))

        event = str(record.get("last_event") or "").strip()
        if event:
            last_events.append(event)

        for line in record.get("log_tail") or []:
            if line:
                log_lines.append(str(line))

        activity = record.get("activity_summary")
        if not isinstance(activity, dict):
            activity = record.get("runtime_activity") if isinstance(record.get("runtime_activity"), dict) else {}
        if isinstance(activity, dict):
            for key, value in activity.items():
                if key in {"recent_events", "recent_activity", "recent_parsed_activity"}:
                    continue
                if key in {"unique_tgids", "tgids"}:
                    if isinstance(value, list):
                        for item in value:
                            parsed = as_int(item if not isinstance(item, dict) else item.get("tgid"))
                            if parsed:
                                activity_unique_tgids.add(parsed)
                    elif isinstance(value, dict):
                        for item in value.keys():
                            parsed = as_int(item)
                            if parsed:
                                activity_unique_tgids.add(parsed)
                    continue
                numeric = as_int(value)
                if numeric is not None:
                    activity_numeric_max[key] = max(activity_numeric_max.get(key, 0), numeric)

    for tgid in activity_unique_tgids:
        tgids.setdefault(tgid, "")

    return {
        "source_files": [str(path) for path in files],
        "snapshot_count": len(records),
        "running_snapshots": running_snapshots,
        "states": dict(states),
        "failure_snapshots": failure_snapshots,
        "control_frequencies_hz": dict(sorted(control_freqs.items())),
        "voice_frequencies_hz": dict(sorted(voice_freqs.items())),
        "tgids": {str(key): value for key, value in sorted(tgids.items())},
        "phase_counts": dict(phase_counts),
        "encrypted_snapshots": encrypted_snapshots,
        "muted_snapshots": muted_snapshots,
        "clear_voice_snapshots": clear_voice_snapshots,
        "warnings": sorted(warnings),
        "last_events": last_events[-10:],
        "log_tail_sample": log_lines[-40:],
        "activity_numeric_max": activity_numeric_max,
    }


def mhz(freq_hz: int) -> str:
    return f"{freq_hz / 1_000_000:.6f} MHz"


def make_report(summary: dict[str, Any], evidence_root: Path, strict: bool) -> tuple[str, int, int, int]:
    passes = 0
    warns = 0
    fails = 0
    lines: list[str] = []
    lines.append("# PI-P25-SCANNER Live Evidence Analysis")
    lines.append("")
    lines.append(f"- Evidence root: `{evidence_root}`")
    lines.append(f"- Snapshot count: {summary['snapshot_count']}")
    lines.append(f"- Source JSON files: {len(summary['source_files'])}")
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
        add("PASS", "status snapshots were readable")
    else:
        add("FAIL", "no status snapshots were found")

    if summary["failure_snapshots"] > 0:
        add("FAIL", f"failure scanner states observed: {summary['failure_snapshots']}")
    else:
        add("PASS", "no decoder/config failure states observed in snapshots")

    if summary["running_snapshots"] > 0:
        add("PASS", f"decoder running snapshots observed: {summary['running_snapshots']}")
    else:
        add("WARN", "no running decoder snapshots observed")

    if summary["control_frequencies_hz"]:
        freqs = ", ".join(mhz(int(freq)) for freq in summary["control_frequencies_hz"].keys())
        add("PASS", f"control frequency evidence observed: {freqs}")
    else:
        add("WARN", "no control-frequency evidence observed")

    if summary["voice_frequencies_hz"]:
        freqs = ", ".join(mhz(int(freq)) for freq in summary["voice_frequencies_hz"].keys())
        add("PASS", f"voice frequency evidence observed: {freqs}")
    else:
        add("WARN", "no voice-frequency evidence observed")

    if summary["tgids"]:
        tgid_text = ", ".join(
            f"{tgid} ({label})" if label else tgid for tgid, label in summary["tgids"].items()
        )
        add("PASS", f"talkgroup evidence observed: {tgid_text}")
    elif strict:
        add("FAIL", "no talkgroup evidence observed in strict mode")
    else:
        add("WARN", "no talkgroup evidence observed")

    if summary["clear_voice_snapshots"] > 0:
        add("PASS", f"clear voice snapshots observed: {summary['clear_voice_snapshots']}")
    else:
        add("WARN", "no clear voice snapshots observed")

    if summary["encrypted_snapshots"] > 0:
        add("PASS", f"encrypted-call metadata observed and counted: {summary['encrypted_snapshots']}")
    else:
        add("PASS", "no encrypted-call metadata observed during this capture")

    if summary["muted_snapshots"] > 0:
        add("PASS", f"muted/skipped snapshots observed: {summary['muted_snapshots']}")
    else:
        add("PASS", "no muted/skipped snapshots observed during this capture")

    lines.append("")
    lines.append("## States")
    if summary["states"]:
        for state, count in sorted(summary["states"].items()):
            lines.append(f"- {state}: {count}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("## Activity Counters")
    counters = summary.get("activity_numeric_max") or {}
    if counters:
        for key, value in sorted(counters.items()):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- no activity counter block found in snapshots")

    lines.append("")
    lines.append("## Recent Events")
    for event in summary.get("last_events") or ["none"]:
        lines.append(f"- {event}")

    lines.append("")
    lines.append("## Warnings")
    for warning in summary.get("warnings") or ["none"]:
        lines.append(f"- {warning}")

    lines.append("")
    lines.append("## OP25 Log Tail Sample")
    log_tail = summary.get("log_tail_sample") or []
    if log_tail:
        lines.append("```text")
        lines.extend(log_tail[-25:])
        lines.append("```")
    else:
        lines.append("- no log-tail sample found")

    lines.append("")
    lines.append(f"SUMMARY: PASS={passes} WARN={warns} FAIL={fails}")
    lines.append("FINAL: PASS" if fails == 0 else "FINAL: FAIL")
    return "\n".join(lines) + "\n", passes, warns, fails


def analyze_path(path: Path, strict: bool, report_dir: Path | None) -> tuple[dict[str, Any], str, int, int, int, Path | None, Path | None]:
    files = collect_json_files(path)
    records: list[dict[str, Any]] = []
    for file_path in files:
        try:
            records.extend(iter_status_records(read_json(file_path)))
        except (OSError, json.JSONDecodeError) as exc:
            records.append(
                {
                    "scanner_state": "evidence_read_error",
                    "warnings": [f"{file_path}: {exc}"],
                }
            )
    summary = summarize(records, files)
    report_text, passes, warns, fails = make_report(summary, path, strict)

    report_path: Path | None = None
    summary_path: Path | None = None
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = utc_stamp()
        report_path = report_dir / f"evidence_analysis_{stamp}.md"
        summary_path = report_dir / f"evidence_analysis_{stamp}.json"
        report_path.write_text(report_text, encoding="utf-8", newline="\n")
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    return summary, report_text, passes, warns, fails, report_path, summary_path


def run_self_test(keep: bool) -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="pi-p25-evidence-selftest-"))
    try:
        evidence = temp_root / "runtime" / "evidence" / "sample"
        evidence.mkdir(parents=True)
        samples = [
            {
                "scanner_state": "running",
                "decoder_process": {"running": True},
                "active_control_frequency_hz": 852750000,
                "p25_phase": "Phase II",
                "activity_summary": {"parsed_status_lines": 1, "control_frequency_updates": 1},
                "last_event": "Decoder process started from validated OP25 command marker",
                "log_tail": ["control channel frequency 852.750000"],
            },
            {
                "scanner_state": "running",
                "decoder_process": {"running": True},
                "active_control_frequency_hz": 852750000,
                "active_voice_frequency_hz": 853275000,
                "active_tgid": 3001,
                "active_talkgroup_label": "Mesa Fire Dispatch",
                "p25_phase": "Phase II",
                "encrypted": False,
                "muted": False,
                "activity_summary": {
                    "parsed_status_lines": 4,
                    "control_frequency_updates": 1,
                    "voice_frequency_updates": 1,
                    "talkgroup_updates": 1,
                    "clear_voice_events": 1,
                    "unique_tgids": [3001],
                },
                "last_event": "Parsed voice grant",
                "log_tail": ["voice grant tgid 3001 frequency 853.275000 label Mesa Fire Dispatch clear"],
            },
            {
                "scanner_state": "running",
                "decoder_process": {"running": True},
                "active_control_frequency_hz": 852750000,
                "active_voice_frequency_hz": 853350000,
                "active_tgid": 4501,
                "active_talkgroup_label": "Encrypted Law TG",
                "p25_phase": "Phase II",
                "encrypted": True,
                "muted": True,
                "activity_summary": {
                    "parsed_status_lines": 5,
                    "encrypted_events": 1,
                    "muted_events": 1,
                    "unique_tgids": [3001, 4501],
                },
                "last_event": "Encrypted call metadata observed",
                "log_tail": ["tgid 4501 encrypted muted"],
            },
        ]
        for index, sample in enumerate(samples, start=1):
            (evidence / f"status_{index:03d}.json").write_text(
                json.dumps(sample, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )

        report_dir = temp_root / "reports"
        summary, report, _passes, _warns, fails, report_path, summary_path = analyze_path(
            evidence,
            strict=True,
            report_dir=report_dir,
        )
        required = [
            summary["snapshot_count"] == 3,
            "3001" in summary["tgids"],
            summary["encrypted_snapshots"] >= 1,
            summary["muted_snapshots"] >= 1,
            report_path is not None and report_path.exists(),
            summary_path is not None and summary_path.exists(),
            "FINAL: PASS" in report,
        ]
        if fails != 0 or not all(required):
            print(report)
            print("FINAL: FAIL")
            return 1
        print("PASS: self-test evidence analyzer produced expected summary")
        print(f"PASS: self-test report path: {report_path}")
        print("SUMMARY: PASS=2 WARN=0 FAIL=0")
        print("FINAL: PASS")
        return 0
    finally:
        if keep:
            print(f"WARN: keeping self-test directory: {temp_root}")
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze PI-P25-SCANNER live activity evidence snapshots")
    parser.add_argument("--path", help="Evidence file or directory to analyze")
    parser.add_argument("--latest", action="store_true", help="Analyze the newest runtime/evidence or capture-report path")
    parser.add_argument("--strict", action="store_true", help="Fail when no talkgroup evidence is found")
    parser.add_argument("--json", action="store_true", help="Print JSON summary instead of Markdown")
    parser.add_argument("--self-test", action="store_true", help="Run an isolated analyzer self-test")
    parser.add_argument("--keep-self-test", action="store_true", help="Keep temporary self-test files")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test(args.keep_self_test)

    if args.path:
        evidence_root = Path(args.path)
    else:
        evidence_root = find_latest_evidence_root() if args.latest or not args.path else None

    if evidence_root is None:
        print("FAIL: no evidence path found; run live activity capture first or pass --path")
        print("SUMMARY: PASS=0 WARN=0 FAIL=1")
        print("FINAL: FAIL")
        return 1
    if not evidence_root.exists():
        print(f"FAIL: evidence path does not exist: {evidence_root}")
        print("SUMMARY: PASS=0 WARN=0 FAIL=1")
        print("FINAL: FAIL")
        return 1

    summary, report, passes, warns, fails, report_path, summary_path = analyze_path(
        evidence_root,
        strict=args.strict,
        report_dir=REPORT_DIR,
    )

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(report, end="")

    if report_path is not None:
        print(f"Report: {report_path}")
    if summary_path is not None:
        print(f"Summary JSON: {summary_path}")
    print(f"SUMMARY: PASS={passes} WARN={warns} FAIL={fails}")
    print("FINAL: PASS" if fails == 0 else "FINAL: FAIL")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
PY_ANALYZE_EVIDENCE
