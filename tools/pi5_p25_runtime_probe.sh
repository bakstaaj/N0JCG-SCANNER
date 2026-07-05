#!/usr/bin/env bash
# Raspberry Pi 5 runtime probe for PI P25 Scanner V0.1B.
# This is a non-invasive validator: it does not install packages or start long-running decoding.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_runtime_probe_reports"
REPORT_FILE="$REPORT_DIR/pi5_p25_runtime_probe_$(date -u +%Y%m%dT%H%M%SZ).txt"

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

printf '=== Pi 5 P25 scanner V0.1B runtime probe ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "tools" && -d "src/pi_p25_scanner" ]]; then
  pass "running from repository root"
else
  fail "run this script from the PI-P25-SCANNER repository root"
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
probe_command lsusb warn
probe_command rtl_test warn
probe_command rtl_eeprom warn
probe_command rtl_fm warn
probe_command rtl_sdr warn
probe_command sox warn
probe_command nc warn
probe_command op25_rx.py warn
probe_command rx.py warn
probe_command multi_rx.py warn

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if ./tools/validate_repo.sh >> "$REPORT_FILE" 2>&1; then
    pass "repository validator passed"
  else
    fail "repository validator failed; see $REPORT_FILE"
  fi
fi

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if PYTHONPATH=src python3 -m pi_p25_scanner.op25_config --config config/p25_systems.example.json --output runtime/op25 --json > "$REPORT_DIR/generated_op25_manifest.json"; then
    pass "OP25 runtime config generation passed"
  else
    fail "OP25 runtime config generation failed"
  fi
fi

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if PYTHONPATH=src python3 -m pi_p25_scanner.decoder_discovery --json > "$REPORT_DIR/op25_discovery.json"; then
    pass "OP25 decoder discovery completed"
    if python3 - <<'PY' "$REPORT_DIR/op25_discovery.json"
import json, sys
payload = json.load(open(sys.argv[1], encoding='utf-8'))
raise SystemExit(0 if payload.get('installed') else 1)
PY
    then
      pass "OP25 candidate found"
    else
      warn "OP25 candidate not found; install/probe is a later milestone"
    fi
  else
    fail "OP25 decoder discovery command failed"
  fi
fi

if command -v lsusb >/dev/null 2>&1; then
  lsusb > "$REPORT_DIR/lsusb.txt" 2>&1 || true
  RTL_ROWS="$(awk 'BEGIN{c=0} /Realtek|RTL2832|NooElec|RTL-SDR|0bda:2838/{c++} END{print c}' "$REPORT_DIR/lsusb.txt")"
  if [[ "$RTL_ROWS" -gt 0 ]]; then
    pass "possible RTL-SDR USB rows detected: $RTL_ROWS"
  else
    warn "no RTL-SDR USB rows detected by lsusb"
  fi
fi

if command -v rtl_test >/dev/null 2>&1; then
  RTL_LOG="$REPORT_DIR/rtl_test_tuner_probe.txt"
  if timeout 10s rtl_test -t > "$RTL_LOG" 2>&1; then
    pass "rtl_test tuner probe completed"
  else
    RC=$?
    if [[ "$RC" -eq 124 ]]; then
      warn "rtl_test probe timed out; see $RTL_LOG"
    else
      warn "rtl_test returned rc=$RC; see $RTL_LOG"
    fi
  fi
else
  warn "rtl_test unavailable; skipped tuner probe"
fi

if command -v rtl_eeprom >/dev/null 2>&1; then
  SERIAL_COUNT=0
  for idx in 0 1 2 3 4 5 6 7; do
    OUT="$REPORT_DIR/rtl_eeprom_device_${idx}.txt"
    if timeout 5s rtl_eeprom -d "$idx" > "$OUT" 2>&1; then
      SERIAL="$(awk -F: '/Serial number/{gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit}' "$OUT" || true)"
      if [[ -n "$SERIAL" ]]; then
        SERIAL_COUNT=$((SERIAL_COUNT + 1))
        pass "RTL device index $idx serial detected: $SERIAL"
      else
        pass "RTL device index $idx readable by rtl_eeprom"
      fi
    else
      RC=$?
      if [[ "$RC" -eq 124 ]]; then
        warn "rtl_eeprom index $idx timed out"
      fi
    fi
  done
  if [[ "$SERIAL_COUNT" -eq 0 ]]; then
    warn "no RTL EEPROM serials parsed; check report files if radios are attached"
  fi
else
  warn "rtl_eeprom unavailable; skipped serial probe"
fi

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
printf 'Report: %s\n' "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
