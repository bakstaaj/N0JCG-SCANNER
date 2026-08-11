#!/usr/bin/env bash
# Validate the checked-in TOPAZ/TRWC Mesa test profile and initializer.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_topaz_trwc_profile_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/topaz_trwc_profile_probe_${STAMP}.txt"
TEMPLATE="config/topaz_trwc_mesa_test.json"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }
finish() {
  printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
    exit 0
  fi
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
}

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"
printf '=== scanner TOPAZ/TRWC profile probe ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "tools" ]]; then
  pass "running from repository root"
else
  fail "run from scanner repository root"
fi

for cmd in python3; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "command available: $cmd"
  else
    fail "missing required command: $cmd"
  fi
done

if [[ -f "$TEMPLATE" ]]; then
  pass "TOPAZ/TRWC test template exists"
else
  fail "TOPAZ/TRWC test template missing"
fi

if [[ -x "tools/p25_init_topaz_trwc_test_config.sh" ]]; then
  pass "TOPAZ/TRWC initializer executable"
else
  fail "TOPAZ/TRWC initializer missing or not executable"
fi

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  finish
fi

if PYTHONPATH=src python3 - "$TEMPLATE" <<'PY_CHECK' | tee -a "$REPORT_FILE"
from __future__ import annotations
import json
import sys
from pathlib import Path
from pi_p25_scanner.config_store import validate_config_payload

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
validate_config_payload(payload)
system = payload["systems"][0]
control = system["control_channels_hz"]
tgids = {int(tg["tgid"]): tg.get("label", "") for tg in system["talkgroups"]}
expected_control = {852750000, 852825000, 853275000, 853350000}
expected_tgids = {3064, 3065, 3066, 3067, 3068, 3069, 3070, 3105, 3899, 3044, 3804, 3049, 2900, 2901, 2902, 2903, 2904, 3107, 3840}
missing_control = sorted(expected_control.difference(control))
missing_tgids = sorted(expected_tgids.difference(tgids))
if missing_control:
    raise SystemExit(f"missing expected control channels: {missing_control}")
if missing_tgids:
    raise SystemExit(f"missing expected talkgroups: {missing_tgids}")
if not system.get("decoder", {}).get("mute_encrypted", False):
    raise SystemExit("mute_encrypted must remain true for TOPAZ/TRWC testing")
print("TOPAZ_TRWC_PROFILE_VALIDATION_PASS")
print("CONTROL_CHANNEL_COUNT=" + str(len(control)))
print("TALKGROUP_COUNT=" + str(len(tgids)))
PY_CHECK
then
  pass "TOPAZ/TRWC profile content validation passed"
else
  fail "TOPAZ/TRWC profile content validation failed"
fi

if ./tools/p25_init_topaz_trwc_test_config.sh --dry-run >> "$REPORT_FILE" 2>&1; then
  pass "TOPAZ/TRWC initializer dry-run passed"
else
  fail "TOPAZ/TRWC initializer dry-run failed"
fi

finish
