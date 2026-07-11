#!/usr/bin/env bash
set -Eeuo pipefail
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
LOG_FILE="${LOG_DIR}/deploy_v0_4d3m_radioreference_picker_${STAMP}.txt"
mkdir -p "$LOG_DIR" 2>/dev/null || true
log(){ printf '%s\n' "$*" | tee -a "$LOG_FILE"; }
pass(){ PASS_COUNT=$((PASS_COUNT+1)); log "PASS: $*"; }
warn(){ WARN_COUNT=$((WARN_COUNT+1)); log "WARN: $*"; }
fail(){ FAIL_COUNT=$((FAIL_COUNT+1)); log "FAIL: $*"; }
finish(){
  log "UPLOAD_FILE_MSYS=${LOG_FILE}"
  log "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || printf '%s' "$LOG_FILE")"
  log "SUMMARY: PASS=${PASS_COUNT} WARN=${WARN_COUNT} FAIL=${FAIL_COUNT}"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then log "FINAL: PASS"; else log "FINAL: FAIL"; fi
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish; exit $rc; fi' ERR
trap finish EXIT

log "=== Deploy V0.4D3M RadioReference US country picker ==="
cd "$(dirname "$0")/.."
[[ -f .env ]] && set -a && source .env && set +a && pass "loaded .env" || warn ".env not found; using shell environment"
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
REMOTE="${PI_USER}@${PI_HOST}"
pass "target fixed to ${REMOTE}:${PI_REPO}"

SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
SCP_BASE=(scp -O -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
if [[ -n "${SSHPASS:-}" && -x "$(command -v sshpass)" ]]; then
  SSH_CMD=(sshpass -e "${SSH_BASE[@]}")
  SCP_CMD=(sshpass -e "${SCP_BASE[@]}")
  pass "using sshpass with SSHPASS"
elif [[ -n "${PI_PASSWORD:-}" && -x "$(command -v sshpass)" ]]; then
  export SSHPASS="$PI_PASSWORD"
  SSH_CMD=(sshpass -e "${SSH_BASE[@]}")
  SCP_CMD=(sshpass -e "${SCP_BASE[@]}")
  pass "using sshpass with PI_PASSWORD"
else
  SSH_CMD=("${SSH_BASE[@]}" -o BatchMode=yes)
  SCP_CMD=("${SCP_BASE[@]}" -o BatchMode=yes)
  warn "no SSHPASS/PI_PASSWORD available; trying SSH key auth only"
fi

"${SSH_CMD[@]}" "$REMOTE" "test -d '$PI_REPO'"
pass "Pi repo reachable without interactive prompt"

TMP_TAR="/tmp/pi_p25_rr_picker_v0_4d3m_${STAMP}.tar"
tar -cf "$TMP_TAR" src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_picker_forced_v0_4d3m.py
pass "built deploy tar"
"${SCP_CMD[@]}" "$TMP_TAR" "$REMOTE:/tmp/pi_p25_rr_picker_v0_4d3m.tar"
pass "copied deploy tar to Pi"
rm -f "$TMP_TAR"

"${SSH_CMD[@]}" "$REMOTE" "cd '$PI_REPO' && mkdir -p runtime/patch_backups/deploy_v0_4d3m_${STAMP} && cp -p src/pi_p25_scanner/backend.py runtime/patch_backups/deploy_v0_4d3m_${STAMP}/backend.py.bak 2>/dev/null || true && tar -xf /tmp/pi_p25_rr_picker_v0_4d3m.tar && python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_picker_forced_v0_4d3m.py && sudo systemctl restart pi-p25-scanner.service && sleep 2"
pass "deployed, compiled, restarted backend"

"${SSH_CMD[@]}" "$REMOTE" "python3 - <<'PY'
import json, urllib.request, sys, time
base='http://127.0.0.1:8070'
for _ in range(20):
    try:
        with urllib.request.urlopen(base + '/api/status', timeout=2) as r:
            status=json.loads(r.read().decode())
        break
    except Exception:
        time.sleep(0.5)
else:
    raise SystemExit('backend /api/status did not respond')
print('STATUS_OK', status.get('ok'), status.get('scanner_state'))
req=urllib.request.Request(base + '/api/radioreference/systems', data=json.dumps({'state':'AZ','county':'Maricopa','city':'Mesa'}).encode(), headers={'Content-Type':'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=45) as r:
    body=r.read().decode()
print(body)
payload=json.loads(body)
if payload.get('picker_parser') != 'us-country-explicit-soap-v0.4d3m':
    raise SystemExit('D3M parser did not take over: ' + str(payload.get('picker_parser')))
PY"
pass "verified D3M parser is active"
