#!/usr/bin/env bash
# Run the current scanner Pi bring-up acceptance checks.
# This script is non-invasive: no package install, no OP25 build, no live decoder launch.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_pi_bringup_acceptance_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/pi_bringup_acceptance_${STAMP}.txt"
STEP_DIR="$REPORT_DIR/steps_${STAMP}"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

mkdir -p "$REPORT_DIR" "$STEP_DIR"
: > "$REPORT_FILE"
printf '=== scanner Pi bring-up acceptance ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "tools" ]]; then
  pass "running from repository root"
else
  fail "run from scanner repository root"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]]; then
  pass "Linux host detected"
else
  warn "not running on Linux; Pi hardware probes may warn or skip"
fi

if [[ -r /etc/os-release ]]; then
  os_pretty="$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-unknown}")"
  printf 'OS: %s\n' "$os_pretty" | tee -a "$REPORT_FILE"
  if printf '%s' "$os_pretty" | grep -qiE 'debian|raspberry pi os'; then
    pass "Debian/Raspberry Pi OS family detected"
  else
    warn "unexpected OS family: $os_pretty"
  fi
else
  warn "/etc/os-release unavailable"
fi

if [[ -r /proc/device-tree/model ]]; then
  model="$(tr -d '\0' < /proc/device-tree/model)"
  printf 'Model: %s\n' "$model" | tee -a "$REPORT_FILE"
  if printf '%s' "$model" | grep -qi 'raspberry pi 5'; then
    pass "Raspberry Pi 5 model detected"
  else
    warn "model is not Raspberry Pi 5: $model"
  fi
else
  warn "device model unavailable"
fi

run_step() {
  local name="$1"
  local required="$2"
  local timeout_seconds="$3"
  shift 3
  local safe_name
  safe_name="$(printf '%s' "$name" | tr -cs 'A-Za-z0-9._-' '_')"
  local log_file="$STEP_DIR/${safe_name}.log"
  printf '\n--- %s ---\n' "$name" >> "$REPORT_FILE"
  printf 'Command:' >> "$REPORT_FILE"
  printf ' %q' "$@" >> "$REPORT_FILE"
  printf '\nLog: %s\n' "$log_file" >> "$REPORT_FILE"

  if timeout "${timeout_seconds}s" "$@" > "$log_file" 2>&1; then
    pass "$name passed"
  else
    local rc=$?
    if [[ "$rc" -eq 124 ]]; then
      if [[ "$required" == "required" ]]; then
        fail "$name timed out after ${timeout_seconds}s"
      else
        warn "$name timed out after ${timeout_seconds}s"
      fi
    else
      if [[ "$required" == "required" ]]; then
        fail "$name failed rc=$rc"
      else
        warn "$name returned rc=$rc"
      fi
    fi
    {
      printf 'Last 30 log lines for %s:\n' "$name"
      tail -n 30 "$log_file" || true
    } >> "$REPORT_FILE"
  fi
}

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
fi

if command -v git >/dev/null 2>&1; then
  pass "git available"
else
  fail "git missing"
fi

if [[ -x ./tools/validate_repo.sh ]]; then
  run_step "repo validation" "required" 180 ./tools/validate_repo.sh
else
  fail "missing executable: ./tools/validate_repo.sh"
fi

if [[ -x ./tools/p25_validate_config.sh ]]; then
  run_step "active config validation" "required" 60 ./tools/p25_validate_config.sh
else
  warn "missing executable: ./tools/p25_validate_config.sh"
fi

if [[ -x ./tools/p25_validate_config_api.sh ]]; then
  run_step "config API smoke validation" "required" 90 ./tools/p25_validate_config_api.sh
else
  warn "missing executable: ./tools/p25_validate_config_api.sh"
fi

if [[ -x ./tools/pi5_p25_preflight.sh ]]; then
  run_step "Pi P25 preflight" "required" 120 ./tools/pi5_p25_preflight.sh
else
  fail "missing executable: ./tools/pi5_p25_preflight.sh"
fi

if [[ -x ./tools/pi5_p25_runtime_probe.sh ]]; then
  run_step "Pi P25 runtime probe" "required" 180 ./tools/pi5_p25_runtime_probe.sh
else
  fail "missing executable: ./tools/pi5_p25_runtime_probe.sh"
fi

if [[ -x ./tools/pi5_p25_op25_install_probe.sh ]]; then
  run_step "OP25 install capability probe" "optional" 180 ./tools/pi5_p25_op25_install_probe.sh
else
  warn "missing executable: ./tools/pi5_p25_op25_install_probe.sh"
fi

if [[ -x ./tools/pi5_p25_rtl_role_probe.sh ]]; then
  run_step "RTL role probe" "optional" 180 ./tools/pi5_p25_rtl_role_probe.sh
else
  warn "missing executable: ./tools/pi5_p25_rtl_role_probe.sh"
fi

if [[ -f runtime/settings/rtl_receiver_roles.detected.json ]]; then
  if python3 - <<'PY'
from __future__ import annotations
import json
from pathlib import Path
path = Path("runtime/settings/rtl_receiver_roles.detected.json")
payload = json.loads(path.read_text(encoding="utf-8"))
devices = payload.get("devices", [])
print(f"DETECTED_RTL_DEVICES={len(devices)}")
for item in devices:
    print(f"RTL_DEVICE index={item.get('runtime_index')} serial={item.get('serial', '')}")
raise SystemExit(0 if isinstance(devices, list) else 1)
PY
  then
    pass "detected RTL evidence JSON parses"
  else
    warn "detected RTL evidence JSON did not parse cleanly"
  fi
else
  warn "detected RTL evidence JSON not present yet"
fi

printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'Step logs: %s\n' "$STEP_DIR" | tee -a "$REPORT_FILE"
printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
