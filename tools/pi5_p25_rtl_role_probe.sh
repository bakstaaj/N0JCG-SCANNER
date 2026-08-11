#!/usr/bin/env bash
# Probe RTL-SDR receiver serial/index evidence for scanner.
# Run from the repository root on Raspberry Pi 5.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_rtl_role_probe_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/rtl_role_probe_${STAMP}.txt"
RTL_TEST_LOG="$REPORT_DIR/rtl_test_${STAMP}.txt"
EEPROM_DIR="$REPORT_DIR/eeprom_${STAMP}"
DETECTED_JSON="runtime/settings/rtl_receiver_roles.detected.json"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

mkdir -p "$REPORT_DIR" "$EEPROM_DIR" runtime/settings
: > "$REPORT_FILE"
printf '=== scanner RTL role probe ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "tools" && -d "config" ]]; then
  pass "running from repository root"
else
  fail "run from scanner repository root"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]]; then
  pass "Linux host detected"
else
  warn "not running on Linux; RTL hardware probing may be unavailable"
fi

if command -v lsusb >/dev/null 2>&1; then
  pass "lsusb available"
  {
    printf '\n--- lsusb ---\n'
    lsusb || true
  } >> "$REPORT_FILE"
else
  warn "lsusb not available"
fi

if command -v rtl_test >/dev/null 2>&1; then
  pass "rtl_test available"
  if timeout 10s rtl_test -t >"$RTL_TEST_LOG" 2>&1; then
    pass "rtl_test tuner probe completed"
  else
    rc=$?
    if [[ "$rc" -eq 124 ]]; then
      warn "rtl_test timed out; partial log recorded: $RTL_TEST_LOG"
    else
      warn "rtl_test returned rc=$rc; log recorded: $RTL_TEST_LOG"
    fi
  fi
else
  warn "rtl_test not available; skipped tuner probe"
fi

if command -v rtl_eeprom >/dev/null 2>&1; then
  pass "rtl_eeprom available"
  for idx in 0 1 2 3 4 5 6 7; do
    log="$EEPROM_DIR/rtl_eeprom_device_${idx}.txt"
    if timeout 5s rtl_eeprom -d "$idx" >"$log" 2>&1; then
      pass "rtl_eeprom readable for device index $idx"
    else
      rc=$?
      if [[ "$idx" -eq 0 ]]; then
        warn "rtl_eeprom index $idx returned rc=$rc; see $log"
      fi
    fi
  done
else
  warn "rtl_eeprom not available; skipped EEPROM serial probe"
fi

if command -v python3 >/dev/null 2>&1; then
  if python3 - "$RTL_TEST_LOG" "$EEPROM_DIR" "$DETECTED_JSON" <<'PY'
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

rtl_test_log = Path(sys.argv[1])
eeprom_dir = Path(sys.argv[2])
out_path = Path(sys.argv[3])

devices: dict[int, dict[str, object]] = {}
if rtl_test_log.exists():
    for line in rtl_test_log.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.search(r"^\s*(\d+):\s*(.+?)(?:,\s*SN:\s*(\S+))?\s*$", line)
        if match:
            idx = int(match.group(1))
            devices.setdefault(idx, {"runtime_index": idx})
            devices[idx]["description"] = match.group(2).strip()
            if match.group(3):
                devices[idx]["serial"] = match.group(3).strip()

serial_patterns = [
    re.compile(r"Serial number:\s*(\S+)", re.IGNORECASE),
    re.compile(r"SN:\s*(\S+)", re.IGNORECASE),
]
for log in sorted(eeprom_dir.glob("rtl_eeprom_device_*.txt")):
    match_idx = re.search(r"_(\d+)\.txt$", log.name)
    if not match_idx:
        continue
    idx = int(match_idx.group(1))
    text = log.read_text(encoding="utf-8", errors="replace")
    if "No supported devices found" in text or "Failed to open" in text:
        continue
    devices.setdefault(idx, {"runtime_index": idx})
    for pattern in serial_patterns:
        serial_match = pattern.search(text)
        if serial_match:
            devices[idx]["serial"] = serial_match.group(1).strip()
            break

payload = {
    "schema_version": 1,
    "source": "tools/pi5_p25_rtl_role_probe.sh",
    "devices": [devices[idx] for idx in sorted(devices)],
    "role_recommendation": {
        "p25_control": "set manually from a stable serial after reviewing detected devices",
        "p25_voice": "optional second stable serial",
    },
}
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
PY
  then
    pass "wrote detected RTL role evidence JSON: $DETECTED_JSON"
  else
    warn "failed to parse RTL role evidence JSON"
  fi
else
  warn "python3 missing; skipped detected JSON generation"
fi

printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'Detected JSON: %s\n' "$DETECTED_JSON" | tee -a "$REPORT_FILE"
printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
