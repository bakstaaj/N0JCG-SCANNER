#!/usr/bin/env bash
# Validate V0.2E runtime status parsing without requiring live RF traffic.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_runtime_status_parser_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/runtime_status_parser_${STAMP}.txt"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"
printf '=== PI-P25-SCANNER runtime status parser probe ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" ]]; then
  pass "running from repository root"
else
  fail "run from PI-P25-SCANNER repository root"
fi

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
fi

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if PYTHONPATH=src python3 -m py_compile src/pi_p25_scanner/runtime_status.py src/pi_p25_scanner/backend.py >> "$REPORT_FILE" 2>&1; then
  pass "runtime parser/backend compile"
else
  fail "runtime parser/backend compile failed"
fi

if PYTHONPATH=src python3 - <<'PY_VALIDATE' >> "$REPORT_FILE" 2>&1
from __future__ import annotations

from pi_p25_scanner.runtime_status import RuntimeStatusParser
from pi_p25_scanner.backend import ScannerManager

parser = RuntimeStatusParser()

control = parser.parse_line("control channel frequency 851.012500")
assert control.control_frequency_hz == 851_012_500, control
assert control.voice_frequency_hz is None, control

voice = parser.parse_line("voice grant tgid 1001 frequency 852.012500 Phase II clear label Fire Dispatch")
assert voice.tgid == 1001, voice
assert voice.voice_frequency_hz == 852_012_500, voice
assert voice.p25_phase == "Phase II", voice
assert voice.encrypted is False, voice
assert voice.muted is False, voice

encrypted = parser.parse_line("encrypted voice tgid=1002 freq=853012500 muted")
assert encrypted.tgid == 1002, encrypted
assert encrypted.voice_frequency_hz == 853_012_500, encrypted
assert encrypted.encrypted is True, encrypted
assert encrypted.muted is True, encrypted

manager = ScannerManager()
manager._append_log("control channel frequency 851.012500")
manager._append_log("voice grant tgid 1001 frequency 852.012500 Phase II clear label Fire Dispatch")
payload = manager.status_payload()
assert payload["active_control_frequency_hz"] == 851_012_500, payload
assert payload["active_voice_frequency_hz"] == 852_012_500, payload
assert payload["active_tgid"] == 1001, payload
assert payload["active_talkgroup_label"] == "Fire Dispatch", payload
assert payload["p25_phase"] == "Phase II", payload
assert payload["encrypted"] is False, payload
assert payload["muted"] is False, payload
assert payload["runtime_status"]["voice_frequency_hz"] == 852_012_500, payload

print("RUNTIME_STATUS_PARSER_PROBE_PASS")
PY_VALIDATE
then
  pass "runtime status parser sample validation passed"
else
  fail "runtime status parser sample validation failed"
fi

printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
