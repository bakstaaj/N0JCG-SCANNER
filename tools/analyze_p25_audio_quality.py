#!/usr/bin/env python3
"""Analyze OP25/browser-audio live-test evidence.

This is a classifier only. It does not attempt decryption or bypass encrypted
traffic. It helps decide whether garbled audio is likely encrypted traffic,
RF/simulcast decode errors, or browser stream gaps.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from statistics import mean
from typing import Any

ENCRYPTED_RE = re.compile(r"encrypted|cipher|algid|keyid|crypt|skip encrypted|algorithm module not found", re.I)
VOICE_RE = re.compile(r"\b(IMBE|AMBE|voice|grant|tgid)\b", re.I)
CONTROL_RE = re.compile(r"control channel|tsbk|trunk|nac|duid", re.I)
GENERIC_ERROR_RE = re.compile(r"timeout|error|errs\s+[1-9]|rs_errs=[1-9]|sync", re.I)
ERRS_RE = re.compile(r"\berrs\s+([0-9]+)\b", re.I)
RS_ERRS_RE = re.compile(r"\brs_errs=([0-9]+)\b", re.I)
BER_RE = re.compile(r"\bber\b[^0-9+-]*([0-9]+(?:\.[0-9]+)?)", re.I)
D_ERROR_RE = re.compile(r"\b(?:d[-_ ]?error|d_err|d-err)\b[^0-9+-]*([+-]?[0-9]+(?:\.[0-9]+)?)", re.I)
FREQ_TRACK_RE = re.compile(r"frequency_tracking[^-+0-9]*([+-]?[0-9]+(?:\.[0-9]+)?)", re.I)


def numeric_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "max": None, "avg": None}
    return {"count": len(values), "max": max(values), "avg": round(mean(values), 3)}


def collect_samples(lines: list[str], regex: re.Pattern[str], limit: int = 8) -> list[str]:
    samples: list[str] = []
    for line in lines:
        if regex.search(line):
            samples.append(line[:220])
            if len(samples) >= limit:
                break
    return samples


def parse_numbers(lines: list[str], regex: re.Pattern[str]) -> list[float]:
    values: list[float] = []
    for line in lines:
        match = regex.search(line)
        if match:
            try:
                values.append(float(match.group(1)))
            except ValueError:
                pass
    return values


def classify(audio: dict[str, Any], counts: dict[str, int], stats: dict[str, dict[str, Any]]) -> tuple[str, list[str], list[str]]:
    evidence: list[str] = []
    next_steps: list[str] = []
    audio_packets = int(audio.get("audio_packets") or 0)
    dropped_by_flag = int(audio.get("audio_dropped_by_flag") or 0)
    flag_one = int(audio.get("flag_one_count") or 0)
    flag_packets = int(audio.get("flag_packets") or 0)
    underruns = int(audio.get("underruns") or 0)
    encrypted = counts["encrypted"]
    generic_error = counts["generic_error"]
    err_values = stats["imbe_errs"]["avg"] or 0
    rs_values = stats["rs_errs"]["avg"] or 0

    if dropped_by_flag > 0 and encrypted > 0:
        evidence.append(f"Flag-gated bridge dropped {dropped_by_flag} audio frames while OP25 logged {encrypted} encryption-related lines.")
        next_steps.append("Keep flag gating enabled; if clear audio returns, integrate encrypted-skip gating into the main backend audio path.")
        return "ENCRYPTED_OR_INVALID_AUDIO_SUPPRESSED_BY_FLAGS", evidence, next_steps
    if encrypted > 0 and audio_packets > 0 and flag_packets > 0:
        evidence.append(f"OP25 logged {encrypted} encryption-related lines and {flag_packets} UDP control flag packets while audio packets were present.")
        next_steps.append("Enable flag-gated browser audio and verify the garbled bursts disappear or become silence.")
        return "LIKELY_ENCRYPTED_OR_FLAGGED_AUDIO_BURSTS", evidence, next_steps
    if generic_error > 500 or err_values > 2 or rs_values > 2:
        evidence.append(f"OP25 generic/decode error count is {generic_error}; IMBE errs avg={err_values}; RS errs avg={rs_values}.")
        next_steps.append("Treat this as RF/simulcast/tuning: try lower gain, antenna repositioning, and PPM/frequency correction tests.")
        return "LIKELY_RF_OR_SIMULCAST_DECODE_ERRORS", evidence, next_steps
    if audio_packets > 0 and underruns > audio_packets * 5:
        evidence.append(f"Audio packets were sparse ({audio_packets}) compared with stream underruns ({underruns}).")
        next_steps.append("This can be normal during no-traffic windows; compare with OP25 voice/encryption counters before changing audio transport.")
        return "SPARSE_AUDIO_WITH_STREAM_SILENCE_GAPS", evidence, next_steps
    if audio_packets > 0:
        evidence.append(f"Audio packets were present ({audio_packets}) but quality evidence is inconclusive.")
        next_steps.append("Run a longer test or capture OP25 log samples around a heard garbled burst.")
        return "AUDIO_PRESENT_QUALITY_INCONCLUSIVE", evidence, next_steps
    evidence.append("No audio packets reached the bridge during the test window.")
    next_steps.append("Wait for activity or verify control-channel lock and talkgroup activity.")
    return "NO_BROWSER_AUDIO_PACKETS", evidence, next_steps


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze PI-P25 OP25/browser audio quality logs")
    parser.add_argument("--op25-log", required=True)
    parser.add_argument("--audio-status-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    op25_path = Path(args.op25_log)
    audio_path = Path(args.audio_status_json)
    lines = op25_path.read_text(encoding="utf-8", errors="replace").splitlines() if op25_path.exists() else []
    audio: dict[str, Any] = json.loads(audio_path.read_text(encoding="utf-8")) if audio_path.exists() else {}

    counts = {
        "encrypted": sum(1 for line in lines if ENCRYPTED_RE.search(line)),
        "voice": sum(1 for line in lines if VOICE_RE.search(line)),
        "generic_error": sum(1 for line in lines if GENERIC_ERROR_RE.search(line)),
        "control": sum(1 for line in lines if CONTROL_RE.search(line)),
    }
    stats = {
        "ber": numeric_stats(parse_numbers(lines, BER_RE)),
        "d_error": numeric_stats(parse_numbers(lines, D_ERROR_RE)),
        "frequency_tracking": numeric_stats([abs(v) for v in parse_numbers(lines, FREQ_TRACK_RE)]),
        "imbe_errs": numeric_stats(parse_numbers(lines, ERRS_RE)),
        "rs_errs": numeric_stats(parse_numbers(lines, RS_ERRS_RE)),
    }
    classification, evidence, next_steps = classify(audio, counts, stats)
    result: dict[str, Any] = {
        "ok": True,
        "classification": classification,
        "op25_log_lines": len(lines),
        "counts": counts,
        "stats": stats,
        "audio": audio,
        "evidence": evidence,
        "next_steps": next_steps,
        "samples": {
            "encryption": collect_samples(lines, ENCRYPTED_RE),
            "errors": collect_samples(lines, GENERIC_ERROR_RE),
            "d_error_ber": collect_samples(lines, re.compile(r"errs|rs_errs|ber|d[-_ ]?error", re.I)),
        },
    }
    Path(args.output_json).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("=== V0.3I Audio Quality Classifier ===")
    print(f"QUALITY_CLASSIFICATION={classification}")
    print(f"OP25_LOG_LINES={len(lines)}")
    print(
        "BER_COUNT={count} BER_MAX={max} BER_AVG={avg}".format(**stats["ber"])
    )
    print(
        "D_ERROR_COUNT={count} D_ERROR_MAX={max} D_ERROR_AVG={avg}".format(**stats["d_error"])
    )
    print(
        "IMBE_ERR_COUNT={count} IMBE_ERR_MAX={max} IMBE_ERR_AVG={avg}".format(**stats["imbe_errs"])
    )
    print(
        "RS_ERR_COUNT={count} RS_ERR_MAX={max} RS_ERR_AVG={avg}".format(**stats["rs_errs"])
    )
    print(
        "FREQUENCY_TRACKING_COUNT={count} FREQUENCY_TRACKING_MAX_ABS_HZ={max}".format(**stats["frequency_tracking"])
    )
    print(
        "OP25_LINE_COUNTS encrypted={encrypted} voice={voice} generic_error={generic_error} control={control}".format(**counts)
    )
    print(
        "BRIDGE_COUNTS packets={packets} audio_packets={audio_packets} flag_packets={flag_packets} "
        "flag_one={flag_one_count} flag_zero={flag_zero_count} audio_dropped_by_flag={audio_dropped_by_flag} "
        "underruns={underruns} silence_chunks_sent={silence_chunks_sent}".format(
            packets=audio.get("packets"),
            audio_packets=audio.get("audio_packets"),
            flag_packets=audio.get("flag_packets"),
            flag_one_count=audio.get("flag_one_count"),
            flag_zero_count=audio.get("flag_zero_count"),
            audio_dropped_by_flag=audio.get("audio_dropped_by_flag"),
            underruns=audio.get("underruns"),
            silence_chunks_sent=audio.get("silence_chunks_sent"),
        )
    )
    print("EVIDENCE:")
    for item in evidence:
        print(f"- {item}")
    print("NEXT_STEPS:")
    for item in next_steps:
        print(f"- {item}")
    for label, sample_lines in result["samples"].items():
        if sample_lines:
            print(f"{label.upper()}_SAMPLES:")
            for line in sample_lines:
                print(f"- {line}")
    print(f"QUALITY_JSON={args.output_json}")
    print("FINAL_QUALITY_CLASSIFIER: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
