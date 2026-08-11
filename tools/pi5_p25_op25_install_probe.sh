#!/usr/bin/env bash
# Pi-side OP25 install/capability evidence probe.
# Run from the scanner repository root on Raspberry Pi 5.
#
# This script is non-invasive. It does not install, build, clone, or start a
# persistent decoder.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_op25_install_probe_reports"
REPORT_FILE="$REPORT_DIR/op25_install_probe_$(date -u +%Y%m%dT%H%M%SZ).txt"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

probe_command() {
  local name="$1"
  local required="${2:-warn}"
  if command -v "$name" >/dev/null 2>&1; then
    pass "command available: $name ($(command -v "$name"))"
  elif [[ "$required" == "fail" ]]; then
    fail "missing required command: $name"
  else
    warn "missing command: $name"
  fi
}

record_command_output() {
  local title="$1"
  shift
  {
    printf '\n--- %s ---\n' "$title"
    "$@" 2>&1 || true
  } >> "$REPORT_FILE"
}

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"

printf '=== Pi P25 OP25 install/capability probe ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "tools" ]]; then
  pass "running from scanner repository root"
else
  fail "run this script from the scanner repository root"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]]; then
  pass "Linux runtime detected"
else
  fail "target runtime must be Linux"
fi

if [[ -r /etc/os-release ]]; then
  OS_PRETTY="$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-unknown}")"
  OS_CODENAME="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-unknown}")"
  pass "OS detected: $OS_PRETTY"
  if [[ "$OS_CODENAME" == "trixie" ]]; then
    pass "Trixie codename detected"
  else
    warn "expected Trixie; detected codename: $OS_CODENAME"
  fi
else
  warn "/etc/os-release not readable"
fi

if [[ -r /proc/device-tree/model ]]; then
  MODEL="$(tr -d '\0' < /proc/device-tree/model)"
  if [[ "$MODEL" == *"Raspberry Pi 5"* ]]; then
    pass "Raspberry Pi 5 detected: $MODEL"
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
probe_command rtl_sdr warn
probe_command rtl_fm warn
probe_command sox warn
probe_command nc warn

for candidate in op25_rx.py rx.py multi_rx.py multi_rx op25; do
  probe_command "$candidate" warn
done

if command -v python3 >/dev/null 2>&1; then
  if PYTHONPATH=src python3 -m pi_p25_scanner.decoder_discovery >> "$REPORT_FILE" 2>&1; then
    pass "decoder discovery module executed"
  else
    fail "decoder discovery module failed"
  fi

  if PYTHONPATH=src python3 -m pi_p25_scanner.op25_config >> "$REPORT_FILE" 2>&1; then
    pass "OP25 runtime config generation executed"
  else
    fail "OP25 runtime config generation failed"
  fi
fi

if [[ -f runtime/op25/trunk.tsv ]]; then
  pass "generated runtime/op25/trunk.tsv exists"
else
  warn "runtime/op25/trunk.tsv not generated"
fi

if [[ -f runtime/op25/talkgroups.tsv ]]; then
  pass "generated runtime/op25/talkgroups.tsv exists"
else
  warn "runtime/op25/talkgroups.tsv not generated"
fi

if command -v lsusb >/dev/null 2>&1; then
  pass "lsusb available"
  record_command_output "lsusb" lsusb
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
      warn "rtl_test timed out; see $RTL_LOG"
    else
      warn "rtl_test returned rc=$RC; see $RTL_LOG"
    fi
  fi
else
  warn "rtl_test unavailable; skipped tuner probe"
fi

if command -v rtl_eeprom >/dev/null 2>&1; then
  EEPROM_LOG="$REPORT_DIR/rtl_eeprom_$(date -u +%Y%m%dT%H%M%SZ).txt"
  if timeout 8s rtl_eeprom >"$EEPROM_LOG" 2>&1; then
    pass "rtl_eeprom probe completed"
  else
    RC=$?
    if [[ "$RC" -eq 124 ]]; then
      warn "rtl_eeprom timed out; see $EEPROM_LOG"
    else
      warn "rtl_eeprom returned rc=$RC; see $EEPROM_LOG"
    fi
  fi
else
  warn "rtl_eeprom unavailable; skipped EEPROM probe"
fi

for path in /usr/local/bin/op25 /usr/local/bin/op25_rx.py /usr/local/bin/rx.py /usr/local/bin/multi_rx.py /usr/bin/op25_rx.py /usr/bin/rx.py /usr/bin/multi_rx.py "$HOME"/op25 "$HOME"/op25/op25/gr-op25_repeater/apps/rx.py; do
  if [[ -e "$path" ]]; then
    pass "possible OP25 path exists: $path"
  else
    warn "possible OP25 path missing: $path"
  fi
done

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
