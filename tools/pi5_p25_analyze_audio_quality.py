#!/usr/bin/env python3
"""Analyze PI-P25 OP25 browser-audio quality evidence.

This tool classifies a listening run without changing audio. It separates likely
RF/decode distortion, encrypted-traffic indicators, browser stream underruns,
and no-traffic gaps using OP25 stderr plus browser-audio bridge counters.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any

NUMBER_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
BER_PATTERNS = [
    re.compile(rf"\bber\b\s*[:=]?\s*(?P<value>{NUMBER_RE})\s*%?", re.IGNORECASE),
    re.compile(rf"\bbit\s+error\s+rate\b\s*[:=]?\s*(?P<value>{NUMBER_RE})\s*%?", re.IGNORECASE),
]
D_ERROR_PATTERNS = [
    re.compile(rf"\bd[\s_-]*(?:err|error)\b\s*[:=]?\s*(?P<value>{NUMBER_RE})", re.IGNORECASE),
    re.compile(rf"\bdemod(?:ulator)?\s+error\b\s*[:=]?\s*(?P<value>{NUMBER_RE})", re.IGNORECASE),
]
FREQ_TRACK_RE = re.compile(r"frequency_tracking\s+(?P<freq_error>-?\d+)\s+(?P<tuning_error>-?\d+)\s+(?P<ppm>-?\d+)\s+(?P<fine>-?\d+)", re.IGNORECASE)
ENCRYPTED_RE = re.compile(r"\b(encrypt(?:ed|ion)?|crypt|algid|keyid|kid|mi=|protected)\b", re.IGNORECASE)
VOICE_RE = re.compile(r"\b(voice|imbe|ambe|tgid|grp[_ -]?v|grant|tdma|duid|lcw|srcaddr|emergency)\b", re.IGNORECASE)
ERROR_RE = re.compile(r"\b(crc|rs_err|decode|sync|timeout|lost|error|err|unrecognized|invalid|failed|frequency_tracking)\b", re.IGNORECASE)
CONTROL_RE = re.compile(r"\b(tsbk|nac|sysid|wacn|control|secondary|adjacent|trunk|cc)\b", re.IGNORECASE)


def read_text(path: str | None) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def load_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def extract_json_after_marker(text: str, marker: str) -> dict[str, Any]:
    idx = text.rfind(marker)
    if idx < 0:
        return {}
    start = text.find("{", idx)
    if start < 0:
        return {}
    depth = 0
    in_string = False
    escape = False
    for pos in range(start, len(text)):
        ch = text[pos]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[start : pos + 1])
                except json.JSONDecodeError:
                    return {}
                return data if isinstance(data, dict) else {}
    return {}


def numeric_values(lines: list[str], patterns: list[re.Pattern[str]]) -> list[float]:
    values: list[float] = []
    for line in lines:
        for pattern in patterns:
            for match in pattern.finditer(line):
                try:
                    values.append(float(match.group("value")))
                except (ValueError, IndexError):
                    continue
    return values


def sample_matching(lines: list[str], pattern: re.Pattern[str], limit: int = 12) -> list[str]:
    samples: list[str] = []
    for line in lines:
        if pattern.search(line):
            stripped = line.strip()
            if stripped:
                samples.append(stripped[:240])
        if len(samples) >= limit:
            break
    return samples


def summarize_values(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "max": None, "avg": None, "median": None}
    return {
        "count": len(values),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "avg": round(sum(values) / len(values), 4),
        "median": round(statistics.median(values), 4),
    }


def classify(bridge: dict[str, Any], metrics: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    reasons: list[str] = []
    next_steps: list[str] = []

    audio_packets = int(bridge.get("audio_packets") or 0)
    packets = int(bridge.get("packets") or 0)
    flag_packets = int(bridge.get("flag_packets") or 0)
    ignored_packets = int(bridge.get("ignored_packets") or 0)
    underruns = int(bridge.get("underruns") or 0)
    silence_chunks = int(bridge.get("silence_chunks_sent") or 0)
    chunks_sent = int(bridge.get("chunks_sent") or 0)
    last_audio_age = bridge.get("last_audio_age_seconds")

    ber = metrics["ber"]
    d_error = metrics["d_error"]
    freq = metrics["frequency_tracking"]
    encrypted_count = metrics["encrypted_line_count"]
    generic_error_count = metrics["generic_error_line_count"]
    voice_count = metrics["voice_line_count"]

    if audio_packets == 0 and packets == 0:
        reasons.append("No OP25 UDP packets reached the browser-audio bridge.")
        next_steps.append("Check whether OP25 locked the control channel and whether the UDP audio command stayed running.")
        return "NO_OP25_AUDIO_PATH", reasons, next_steps

    if audio_packets == 0 and packets > 0:
        reasons.append(f"Bridge saw {packets} UDP packets but no 320-byte PCM audio frames.")
        if encrypted_count > 0:
            reasons.append(f"OP25 log contains {encrypted_count} encryption-related lines.")
            return "LIKELY_ENCRYPTED_OR_MUTED_TRAFFIC", reasons, ["Keep encrypted calls muted/skipped and verify clear talkgroup whitelist/discovery."]
        next_steps.append("Run longer or broaden clear talkgroups; this can be no clear voice grants during the window.")
        return "NO_CLEAR_AUDIO_DETECTED", reasons, next_steps

    if ber["count"] and (ber["max"] or 0) >= 5.0:
        reasons.append(f"BER metric is high: max={ber['max']} avg={ber['avg']} count={ber['count']}.")
        next_steps.append("Treat as RF/decode quality first: antenna placement, RTL gain, PPM/fine tuning, or simulcast mitigation.")
        return "LIKELY_RF_OR_SIMULCAST_DECODE_ERRORS", reasons, next_steps

    if d_error["count"] and (d_error["max"] or 0) >= 10.0:
        reasons.append(f"D-Error metric is high: max={d_error['max']} avg={d_error['avg']} count={d_error['count']}.")
        next_steps.append("Treat as RF/decode quality first: antenna placement, RTL gain, PPM/fine tuning, or simulcast mitigation.")
        return "LIKELY_RF_OR_SIMULCAST_DECODE_ERRORS", reasons, next_steps

    if freq["count"] and (freq["max_abs_freq_error"] or 0) >= 200:
        reasons.append(f"OP25 frequency_tracking showed freq error up to {freq['max_abs_freq_error']} Hz.")
        next_steps.append("Check RTL PPM/fine tuning and whether simulcast multipath is causing unstable symbol timing.")
        return "POSSIBLE_TUNING_OR_SIMULCAST_ERROR", reasons, next_steps

    if encrypted_count > 0 and audio_packets > 0:
        reasons.append(f"Audio packets were present, and OP25 log has {encrypted_count} encryption-related lines.")
        next_steps.append("Confirm encrypted calls are being skipped/muted and not being streamed as audio bursts.")
        return "POSSIBLE_ENCRYPTED_BURSTS", reasons, next_steps

    if chunks_sent and underruns > max(200, chunks_sent // 3):
        reasons.append(f"Bridge underruns are high: underruns={underruns}, chunks_sent={chunks_sent}, silence_chunks={silence_chunks}.")
        next_steps.append("This looks like stream starvation/gaps rather than encrypted audio; compare with OP25 voice grant timing.")
        return "LIKELY_STREAM_GAPS_OR_NO_TRAFFIC", reasons, next_steps

    if generic_error_count > 20 and voice_count > 0:
        reasons.append(f"OP25 produced voice-related lines plus {generic_error_count} error/sync/CRC-style lines.")
        next_steps.append("Inspect OP25 log samples in the report; RF/simulcast remains plausible even without explicit BER metrics.")
        return "POSSIBLE_RF_DECODE_ERRORS", reasons, next_steps

    if not ber["count"] and not d_error["count"]:
        reasons.append("No explicit BER or D-Error metrics were found in the OP25 log.")
        next_steps.append("Re-run with --op25-verbosity 10 to try to expose OP25 frequency/error diagnostics in the log.")

    if last_audio_age is not None:
        reasons.append(f"Last audio frame age at final status was {last_audio_age} seconds.")
    reasons.append(f"Bridge saw audio_packets={audio_packets}, flag_packets={flag_packets}, ignored_packets={ignored_packets}.")
    return "AUDIO_PRESENT_QUALITY_INCONCLUSIVE", reasons, next_steps


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify PI-P25 browser-audio live-test quality evidence")
    parser.add_argument("--op25-log", required=True)
    parser.add_argument("--bridge-status-json")
    parser.add_argument("--live-report")
    parser.add_argument("--output-json")
    args = parser.parse_args()

    op25_text = read_text(args.op25_log)
    report_text = read_text(args.live_report)
    bridge = load_json(args.bridge_status_json)
    if not bridge and report_text:
        bridge = extract_json_after_marker(report_text, "FINAL_AUDIO_STATUS")

    lines = [line for line in op25_text.splitlines() if line.strip()]
    ber_values = numeric_values(lines, BER_PATTERNS)
    d_error_values = numeric_values(lines, D_ERROR_PATTERNS)
    freq_values = []
    freq_samples = []
    for line in lines:
        m = FREQ_TRACK_RE.search(line)
        if m:
            freq_values.append(abs(int(m.group("freq_error"))))
            if len(freq_samples) < 12:
                freq_samples.append(line.strip()[:240])

    metrics = {
        "op25_log_path": args.op25_log,
        "op25_log_lines": len(lines),
        "ber": summarize_values(ber_values),
        "d_error": summarize_values(d_error_values),
        "frequency_tracking": {
            "count": len(freq_values),
            "max_abs_freq_error": max(freq_values) if freq_values else None,
            "avg_abs_freq_error": round(sum(freq_values) / len(freq_values), 2) if freq_values else None,
        },
        "encrypted_line_count": sum(1 for line in lines if ENCRYPTED_RE.search(line)),
        "voice_line_count": sum(1 for line in lines if VOICE_RE.search(line)),
        "generic_error_line_count": sum(1 for line in lines if ERROR_RE.search(line)),
        "control_line_count": sum(1 for line in lines if CONTROL_RE.search(line)),
        "samples": {
            "ber_or_d_error": sample_matching(lines, re.compile(r"ber|d[\s_-]*(?:err|error)|bit\s+error", re.IGNORECASE)),
            "frequency_tracking": freq_samples,
            "encrypted": sample_matching(lines, ENCRYPTED_RE),
            "voice": sample_matching(lines, VOICE_RE),
            "generic_error": sample_matching(lines, ERROR_RE),
        },
    }

    classification, reasons, next_steps = classify(bridge, metrics)
    result = {
        "ok": True,
        "classification": classification,
        "reasons": reasons,
        "next_steps": next_steps,
        "bridge_status": bridge,
        "metrics": metrics,
    }

    print("\n=== V0.3H Audio Quality Classifier ===")
    print(f"QUALITY_CLASSIFICATION={classification}")
    print(f"OP25_LOG_LINES={metrics['op25_log_lines']}")
    print(f"BER_COUNT={metrics['ber']['count']} BER_MAX={metrics['ber']['max']} BER_AVG={metrics['ber']['avg']}")
    print(f"D_ERROR_COUNT={metrics['d_error']['count']} D_ERROR_MAX={metrics['d_error']['max']} D_ERROR_AVG={metrics['d_error']['avg']}")
    print(
        "FREQUENCY_TRACKING_COUNT="
        f"{metrics['frequency_tracking']['count']} "
        f"FREQUENCY_TRACKING_MAX_ABS_HZ={metrics['frequency_tracking']['max_abs_freq_error']}"
    )
    print(
        "OP25_LINE_COUNTS "
        f"encrypted={metrics['encrypted_line_count']} "
        f"voice={metrics['voice_line_count']} "
        f"generic_error={metrics['generic_error_line_count']} "
        f"control={metrics['control_line_count']}"
    )
    print(
        "BRIDGE_COUNTS "
        f"packets={bridge.get('packets')} "
        f"audio_packets={bridge.get('audio_packets')} "
        f"flag_packets={bridge.get('flag_packets')} "
        f"ignored_packets={bridge.get('ignored_packets')} "
        f"underruns={bridge.get('underruns')} "
        f"silence_chunks_sent={bridge.get('silence_chunks_sent')}"
    )
    print("EVIDENCE:")
    for reason in reasons:
        print(f"- {reason}")
    if next_steps:
        print("NEXT_STEPS:")
        for step in next_steps:
            print(f"- {step}")
    if metrics["samples"]["ber_or_d_error"]:
        print("D_ERROR_BER_SAMPLES:")
        for sample in metrics["samples"]["ber_or_d_error"][:8]:
            print(f"- {sample}")
    if metrics["samples"]["encrypted"]:
        print("ENCRYPTION_SAMPLES:")
        for sample in metrics["samples"]["encrypted"][:8]:
            print(f"- {sample}")
    if metrics["samples"]["generic_error"]:
        print("ERROR_SAMPLES:")
        for sample in metrics["samples"]["generic_error"][:8]:
            print(f"- {sample}")

    if args.output_json:
        Path(args.output_json).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"QUALITY_JSON={args.output_json}")
    print("FINAL_QUALITY_CLASSIFIER: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
