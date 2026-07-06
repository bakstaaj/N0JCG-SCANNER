#!/usr/bin/env bash
# Deploy V0.3S backend UDP-output integration and raw audio bridge service to the Pi.
# Run from MSYS2 UCRT64 repository root.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
REPORT_FILE="${REPORT_DIR}/deploy_raw_audio_service_v0_3s_${STAMP}.txt"
PI_HOST="${PI_HOST:-pi@PI-SDR}"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
TARBALL="/tmp/pi_p25_v0_3s_raw_audio_${STAMP}.tgz"
REMOTE_TARBALL="/tmp/pi_p25_v0_3s_raw_audio_${STAMP}.tgz"

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"
pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
finish() {
  printf 'UPLOAD_FILE_MSYS=%s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
  printf 'UPLOAD_FILE_WINDOWS=%s\n' "$(cygpath -w "$REPORT_FILE" 2>/dev/null || printf '%s' "$REPORT_FILE")" | tee -a "$REPORT_FILE"
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
    exit 0
  fi
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
}
trap 'rc=$?; if [[ $rc -ne 0 && ${FAIL_COUNT:-0} -eq 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish; fi' EXIT

printf '=== PI-P25-SCANNER V0.3S raw audio service deploy ===\n' | tee -a "$REPORT_FILE"
printf 'Started UTC: %s\n' "$STAMP" | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "tools" ]]; then
  pass "running from repository root"
else
  fail "run from PI-P25-SCANNER repository root"
  finish
fi

if [[ -f tools/msys2_env_common.sh ]]; then
  # shellcheck disable=SC1091
  . tools/msys2_env_common.sh
  warn "loaded tools/msys2_env_common.sh"
fi

for cmd in ssh scp tar python3; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "command available: $cmd"
  else
    fail "missing command: $cmd"
  fi
done
if [[ -n "${PI_PASSWORD:-}" ]] && command -v sshpass >/dev/null 2>&1; then
  SSH=(sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=no)
  SCP=(sshpass -p "$PI_PASSWORD" scp -O -o StrictHostKeyChecking=no)
  pass "using sshpass with PI_PASSWORD from environment/.env"
else
  SSH=(ssh -o StrictHostKeyChecking=no)
  SCP=(scp -O -o StrictHostKeyChecking=no)
  warn "PI_PASSWORD/sshpass not available; using default SSH auth"
fi

FILES=(
  src/pi_p25_scanner/backend.py
  tools/pi5_p25_browser_audio_raw_bridge_server.py
  tools/pi5_p25_raw_audio_bridge_service_install.sh
)
for file in "${FILES[@]}"; do
  if [[ -f "$file" ]]; then
    pass "deploy file exists: $file"
  else
    fail "missing deploy file: $file"
  fi
done
if [[ "$FAIL_COUNT" -ne 0 ]]; then
  finish
fi

python3 -m py_compile src/pi_p25_scanner/backend.py tools/pi5_p25_browser_audio_raw_bridge_server.py >>"$REPORT_FILE" 2>&1 && pass "local Python compile passed" || fail "local Python compile failed"
bash -n tools/pi5_p25_raw_audio_bridge_service_install.sh >>"$REPORT_FILE" 2>&1 && pass "local service installer syntax passed" || fail "local service installer syntax failed"
if [[ "$FAIL_COUNT" -ne 0 ]]; then
  finish
fi

tar -czf "$TARBALL" "${FILES[@]}"
pass "created deploy tarball: $TARBALL"
"${SCP[@]}" "$TARBALL" "${PI_HOST}:${REMOTE_TARBALL}" >>"$REPORT_FILE" 2>&1
pass "copied deploy tarball to ${PI_HOST}:${REMOTE_TARBALL}"

"${SSH[@]}" "$PI_HOST" "cd '$PI_REPO' && tar -xzf '$REMOTE_TARBALL' && chmod +x tools/pi5_p25_raw_audio_bridge_service_install.sh && python3 -m py_compile src/pi_p25_scanner/backend.py tools/pi5_p25_browser_audio_raw_bridge_server.py && ./tools/pi5_p25_raw_audio_bridge_service_install.sh --install --yes && sudo systemctl restart pi-p25-scanner.service && sleep 2 && python3 - <<'PY_REMOTE'\nimport json, urllib.request\nfor url in ['http://127.0.0.1:8070/api/status', 'http://127.0.0.1:8072/api/audio/status']:\n    with urllib.request.urlopen(url, timeout=5) as r:\n        data = r.read(512)\n    print('PROBE_OK', url, len(data))\nPY_REMOTE" >>"$REPORT_FILE" 2>&1
pass "remote deploy, service install, backend restart, and probes passed"

finish
