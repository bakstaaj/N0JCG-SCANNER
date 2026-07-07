#!/usr/bin/env bash
# Install optional RadioReference SOAP client dependency on the Raspberry Pi.
set -Eeuo pipefail
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_radioreference_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/install_radioreference_deps_${STAMP}.txt"
mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"
pass(){ printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT+1)); }
finish(){ printf 'REPORT=%s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"; printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"; [[ "$FAIL_COUNT" -eq 0 ]] && { printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"; exit 0; }; printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"; exit 1; }
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "dependency install aborted line $LINENO rc=$rc"; finish; fi' ERR
sudo_cmd(){ if [[ "$(id -u)" -eq 0 ]]; then "$@"; elif sudo -n true >/dev/null 2>&1; then sudo "$@"; elif [[ -n "${SUDO_PASSWORD:-}" ]]; then printf '%s\n' "$SUDO_PASSWORD" | sudo -S "$@"; else sudo "$@"; fi; }
if python3 - <<'PY' >/dev/null 2>&1
import zeep
PY
then
  pass "python3-zeep already installed"
  finish
fi
if command -v apt-get >/dev/null 2>&1; then
  pass "apt-get available"
  sudo_cmd apt-get update >>"$REPORT_FILE" 2>&1
  sudo_cmd apt-get install -y python3-zeep >>"$REPORT_FILE" 2>&1
  pass "installed python3-zeep via apt"
else
  fail "apt-get unavailable; install the Python zeep package manually"
fi
python3 - <<'PY' >>"$REPORT_FILE" 2>&1
import zeep
print('zeep', getattr(zeep, '__version__', 'unknown'))
PY
pass "verified zeep import"
finish
