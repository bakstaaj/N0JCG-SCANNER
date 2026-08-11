#!/usr/bin/env bash
# Raspberry Pi 5 P25 scanner preflight.
# Run from the scanner repository root on the target Pi.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_preflight_reports"
REPORT_FILE="$REPORT_DIR/pi5_p25_preflight_$(date -u +%Y%m%dT%H%M%SZ).txt"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }
probe_command() {
  local name="$1"
  local level="${2:-warn}"
  if command -v "$name" >/dev/null 2>&1; then
    pass "command available: $name"
  elif [[ "$level" == "fail" ]]; then
    fail "missing required command: $name"
  else
    warn "missing optional/runtime command: $name"
  fi
}

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"

printf '=== Pi 5 P25 scanner preflight ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "tools" && -d "config" ]]; then
  pass "running from repository root"
else
  fail "run this script from the scanner repository root"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]]; then
  pass "Linux host detected"
else
  fail "target runtime must be Linux on Raspberry Pi"
fi

if [[ -r /etc/os-release ]]; then
  OS_PRETTY="$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-unknown}")"
  OS_CODENAME="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-unknown}")"
  pass "OS detected: $OS_PRETTY"
  if [[ "$OS_CODENAME" == "trixie" ]]; then
    pass "Debian/Raspberry Pi OS Trixie codename detected"
  else
    warn "expected Trixie; detected codename: $OS_CODENAME"
  fi
else
  warn "/etc/os-release not readable"
fi

if [[ -r /proc/device-tree/model ]]; then
  MODEL="$(tr -d '\0' < /proc/device-tree/model)"
  if [[ "$MODEL" == *"Raspberry Pi 5"* ]]; then
    pass "Raspberry Pi 5 model detected: $MODEL"
  else
    warn "not detected as Raspberry Pi 5: $MODEL"
  fi
else
  warn "Raspberry Pi model file not readable"
fi

probe_command python3 fail
probe_command git warn
probe_command rtl_test warn
probe_command rtl_eeprom warn
probe_command rtl_fm warn
probe_command rtl_sdr warn
probe_command sox warn
probe_command nc warn
probe_command op25_rx.py warn
probe_command rx.py warn

if command -v lsusb >/dev/null 2>&1; then
  USB_ROWS="$(lsusb | wc -l | tr -d ' ')"
  pass "lsusb available; USB device rows: $USB_ROWS"
  RTL_ROWS="$(lsusb | awk 'BEGIN{c=0} /Realtek|RTL2832|NooElec|RTL-SDR|0bda:2838/{c++} END{print c}')"
  if [[ "$RTL_ROWS" -gt 0 ]]; then
    pass "possible RTL-SDR USB rows detected: $RTL_ROWS"
  else
    warn "no RTL-SDR USB rows detected by lsusb"
  fi
else
  warn "lsusb not available"
fi

if command -v rtl_test >/dev/null 2>&1; then
  RTL_LOG="$REPORT_DIR/rtl_test_$(date -u +%Y%m%dT%H%M%SZ).txt"
  if timeout 8s rtl_test -t >"$RTL_LOG" 2>&1; then
    pass "rtl_test tuner probe completed"
  else
    RC=$?
    if [[ "$RC" -eq 124 ]]; then
      warn "rtl_test probe timed out; see $RTL_LOG"
    else
      warn "rtl_test probe returned rc=$RC; see $RTL_LOG"
    fi
  fi
else
  warn "rtl_test unavailable; skipped tuner probe"
fi

if [[ -f config/p25_systems.example.json ]]; then
  if python3 -m json.tool config/p25_systems.example.json >/dev/null; then
    pass "example P25 config JSON validates"
  else
    fail "example P25 config JSON invalid"
  fi
else
  fail "missing config/p25_systems.example.json"
fi

if bash -n tools/pi5_p25_preflight.sh; then
  pass "self bash syntax check passed"
else
  fail "self bash syntax check failed"
fi

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
printf 'Report: %s\n' "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
