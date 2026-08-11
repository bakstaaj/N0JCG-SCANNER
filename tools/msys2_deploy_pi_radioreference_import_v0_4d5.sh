#!/usr/bin/env bash
set -Eeuo pipefail
PATCH_NAME="deploy_v0_4d5_rr_import_site_frequencies"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/${PATCH_NAME}_${STAMP}.txt"
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
log(){ printf '%s\n' "$*" | tee -a "$LOG_FILE"; }
pass(){ PASS_COUNT=$((PASS_COUNT+1)); log "PASS: $*"; }
warn(){ WARN_COUNT=$((WARN_COUNT+1)); log "WARN: $*"; }
fail(){ FAIL_COUNT=$((FAIL_COUNT+1)); log "FAIL: $*"; }
finish(){
  log "UPLOAD_FILE_MSYS=$LOG_FILE"
  log "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || printf '%s' "$LOG_FILE")"
  log "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then log "FINAL: PASS"; else log "FINAL: FAIL"; fi
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; fi; finish; exit $rc' EXIT

log "=== Deploy V0.4D5 RadioReference import site-frequency fix ==="
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if [[ -f .env ]]; then set -a; # shellcheck disable=SC1091
  source .env; set +a; pass "loaded .env"; fi
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
pass "target fixed to ${PI_USER}@${PI_HOST}:${PI_REPO}"

SSH_BASE=(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes)
SCP_BASE=(scp -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes)
if command -v sshpass >/dev/null 2>&1 && [[ -n "${SSHPASS:-${PI_PASSWORD:-}}" ]]; then
  export SSHPASS="${SSHPASS:-$PI_PASSWORD}"
  SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
  SCP_BASE=(sshpass -e scp -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
  pass "using sshpass with environment password"
else
  warn "no SSHPASS/PI_PASSWORD available; trying SSH key auth only without prompts"
fi

"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "test -d '$PI_REPO'"
pass "Pi repo reachable without interactive prompt"

tar czf /tmp/pi_p25_v0_4d5_rr_import.tgz src/pi_p25_scanner/radioreference_import.py
pass "built deploy tar"
"${SCP_BASE[@]}" /tmp/pi_p25_v0_4d5_rr_import.tgz "${PI_USER}@${PI_HOST}:/tmp/pi_p25_v0_4d5_rr_import.tgz"
pass "copied deploy tar to Pi"

"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "cd '$PI_REPO' && mkdir -p runtime/patch_backups/deploy_v0_4d5_${STAMP} && cp -f src/pi_p25_scanner/radioreference_import.py runtime/patch_backups/deploy_v0_4d5_${STAMP}/radioreference_import.py.bak && tar xzf /tmp/pi_p25_v0_4d5_rr_import.tgz && python3 -m py_compile src/pi_p25_scanner/radioreference_import.py && grep -q 'explicit-site-frequency-v0.4d5' src/pi_p25_scanner/radioreference_import.py && sudo systemctl restart pi-p25-scanner.service && sleep 2"
pass "deployed, compiled, marker verified, restarted backend"

"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "python3 - <<'PY'
import json, urllib.request
status=json.load(urllib.request.urlopen('http://127.0.0.1:8070/api/status', timeout=5))
print('STATUS_OK', status.get('ok'), status.get('scanner_state'))
rr=json.load(urllib.request.urlopen('http://127.0.0.1:8070/api/radioreference/status', timeout=8))
print(json.dumps({'rr_ok': rr.get('ok'), 'configured': rr.get('configured'), 'zeep': rr.get('zeep')}, indent=2, sort_keys=True))
PY"
pass "backend and RadioReference status probes passed"
