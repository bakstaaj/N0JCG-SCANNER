#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_NAME="deploy_v0_4d3j_radioreference_picker"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/${SCRIPT_NAME}_${STAMP}.txt"
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
log(){ printf '%s\n' "$*" | tee -a "$LOG_FILE"; }
pass(){ PASS_COUNT=$((PASS_COUNT+1)); log "PASS: $*"; }
warn(){ WARN_COUNT=$((WARN_COUNT+1)); log "WARN: $*"; }
fail(){ FAIL_COUNT=$((FAIL_COUNT+1)); log "FAIL: $*"; }
finish(){
  log "UPLOAD_FILE_MSYS=$LOG_FILE"
  log "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || printf '%s' "$LOG_FILE")"
  log "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [ "$FAIL_COUNT" -eq 0 ]; then log "FINAL: PASS"; else log "FINAL: FAIL"; fi
}
trap 'rc=$?; if [ $rc -ne 0 ]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish; exit $rc; fi' ERR
trap 'finish' EXIT

PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
[ -f .env ] && set -a && . ./.env && set +a && pass "loaded .env" || warn ".env not found; continuing"
PI_HOST="192.168.254.63"
pass "target fixed to ${PI_USER}@${PI_HOST}:${PI_REPO}"

SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
SCP_BASE=(scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [ -n "${SSHPASS:-}" ] || [ -n "${PI_PASSWORD:-}" ]; then
  command -v sshpass >/dev/null 2>&1 || { fail "sshpass is required when SSHPASS/PI_PASSWORD is set"; exit 1; }
  export SSHPASS="${SSHPASS:-${PI_PASSWORD:-}}"
  SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  SCP_BASE=(sshpass -e scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  pass "using sshpass non-interactively"
else
  SSH_BASE=(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  SCP_BASE=(scp -O -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  warn "SSHPASS/PI_PASSWORD not set; trying SSH key auth only without prompting"
fi

"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "test -d '$PI_REPO'"
pass "Pi repo reachable without interactive prompt"

python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_picker_runtime.py
pass "local py_compile passed"

TMP_TAR="/tmp/pi_p25_v0_4d3j_rr_picker_${STAMP}.tar"
tar -cf "$TMP_TAR" src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_picker_runtime.py
"${SCP_BASE[@]}" "$TMP_TAR" "${PI_USER}@${PI_HOST}:/tmp/"
pass "copied deployment tar"

"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "cd '$PI_REPO' && mkdir -p runtime/patch_backups/deploy_v0_4d3j_${STAMP} && cp -p src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_picker_runtime.py runtime/patch_backups/deploy_v0_4d3j_${STAMP}/ 2>/dev/null || true && tar -xf /tmp/$(basename "$TMP_TAR") && python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_picker_runtime.py && sudo systemctl restart pi-p25-scanner.service"
pass "deployed files and restarted backend"

sleep 2
"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "curl -fsS --max-time 5 http://127.0.0.1:8070/api/status >/dev/null"
pass "backend /api/status reachable"

RESP_FILE="/tmp/pi_p25_rr_systems_${STAMP}.json"
"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "python3 - <<'PY' > '$RESP_FILE'
import json, urllib.request
payload=json.dumps({'state':'AZ','county':'Maricopa','city':'Mesa'}).encode()
req=urllib.request.Request('http://127.0.0.1:8070/api/radioreference/systems', data=payload, headers={'Content-Type':'application/json'}, method='POST')
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(r.read().decode())
except Exception as e:
    print(json.dumps({'ok':False,'probe_error':str(e)}))
PY
cat '$RESP_FILE'"
pass "printed RadioReference systems probe response"
