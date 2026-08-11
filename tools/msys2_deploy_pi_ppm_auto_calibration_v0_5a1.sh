#!/usr/bin/env bash
set -Eeuo pipefail
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
LOG_FILE="$LOG_DIR/deploy_v0_5a1_ppm_auto_calibration_${STAMP}.txt"
mkdir -p "$LOG_DIR" 2>/dev/null || true
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
log(){ printf '%s\n' "$*" | tee -a "$LOG_FILE"; }
pass(){ PASS_COUNT=$((PASS_COUNT+1)); log "PASS: $*"; }
warn(){ WARN_COUNT=$((WARN_COUNT+1)); log "WARN: $*"; }
fail(){ FAIL_COUNT=$((FAIL_COUNT+1)); log "FAIL: $*"; }
finish(){ log "UPLOAD_FILE_MSYS=$LOG_FILE"; log "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || printf '%s' "$LOG_FILE")"; log "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; [[ $FAIL_COUNT -eq 0 ]] && log "FINAL: PASS" || log "FINAL: FAIL"; }
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; fi; finish; exit $rc' EXIT

log "=== Deploy V0.5A1 automated PPM calibration ==="
[[ -f .env ]] && { set -a; source ./.env; set +a; pass "loaded .env"; }
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
pass "target fixed to ${PI_USER}@${PI_HOST}:${PI_REPO}"

SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
SCP_BASE=(scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
if [[ -n "${SSHPASS:-}" ]] && command -v sshpass >/dev/null 2>&1; then
  SSH=(sshpass -e "${SSH_BASE[@]}")
  SCP=(sshpass -e "${SCP_BASE[@]}")
  pass "using sshpass with SSHPASS"
elif [[ -n "${PI_PASSWORD:-}" ]] && command -v sshpass >/dev/null 2>&1; then
  export SSHPASS="$PI_PASSWORD"
  SSH=(sshpass -e "${SSH_BASE[@]}")
  SCP=(sshpass -e "${SCP_BASE[@]}")
  pass "using sshpass with PI_PASSWORD"
else
  SSH=("${SSH_BASE[@]}" -o BatchMode=yes)
  SCP=("${SCP_BASE[@]}" -o BatchMode=yes)
  warn "no SSHPASS/PI_PASSWORD available; using SSH key auth only and will not prompt"
fi

"${SSH[@]}" "${PI_USER}@${PI_HOST}" "test -d '$PI_REPO'"
pass "Pi repo reachable without interactive prompt"

tmp_tar="/tmp/pi_p25_v0_5a1_ppm_${STAMP}.tar.gz"
tar -czf "$tmp_tar" src/pi_p25_scanner/backend.py src/pi_p25_scanner/ppm_calibration.py web/app.js
pass "built deploy tar"
"${SCP[@]}" "$tmp_tar" "${PI_USER}@${PI_HOST}:/tmp/pi_p25_v0_5a1_ppm.tar.gz"
pass "copied deploy tar to Pi"

"${SSH[@]}" "${PI_USER}@${PI_HOST}" "cd '$PI_REPO' && mkdir -p runtime/patch_backups/deploy_v0_5a1_${STAMP} && cp -p src/pi_p25_scanner/backend.py web/app.js runtime/patch_backups/deploy_v0_5a1_${STAMP}/ 2>/dev/null || true && tar -xzf /tmp/pi_p25_v0_5a1_ppm.tar.gz && python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/ppm_calibration.py && if command -v node >/dev/null 2>&1; then node --check web/app.js; fi && sudo systemctl restart pi-p25-scanner.service && sleep 2"
pass "deployed, compiled, restarted backend"

status_json="$(${SSH[@]} "${PI_USER}@${PI_HOST}" "curl -fsS --max-time 8 http://127.0.0.1:8070/api/status")"
printf '%s\n' "$status_json" | python3 -c 'import json,sys; p=json.load(sys.stdin); print("STATUS_OK", p.get("ok"), p.get("scanner_state"))' | tee -a "$LOG_FILE"
ppm_status="$(${SSH[@]} "${PI_USER}@${PI_HOST}" "curl -fsS --max-time 8 http://127.0.0.1:8070/api/calibration/ppm/status")"
printf '%s\n' "$ppm_status" | python3 -m json.tool | tee -a "$LOG_FILE"
pass "verified PPM calibration endpoint"
