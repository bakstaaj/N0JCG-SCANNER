#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_NAME="deploy_v0_4d3k_radioreference_picker"
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
  log "UPLOAD_FILE_WINDOWS=C:\\Users\\jim\\Downloads\\pi-p25-command-logs\\${SCRIPT_NAME}_${STAMP}.txt"
  log "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then log "FINAL: PASS"; else log "FINAL: FAIL"; fi
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish; exit $rc; fi' ERR
trap 'finish' EXIT

log "=== Deploy V0.4D3K RadioReference forced picker ==="
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
[[ -f .env ]] && set -a && source .env && set +a && pass "loaded .env" || warn ".env not found; using shell environment only"
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
pass "target fixed to ${PI_USER}@${PI_HOST}:${PI_REPO}"

SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8)
SCP_BASE=(scp -O -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8)
if [[ -n "${SSHPASS:-}" || -n "${PI_PASSWORD:-}" ]]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    fail "SSHPASS/PI_PASSWORD is set but sshpass is not installed in MSYS2"
    exit 1
  fi
  export SSHPASS="${SSHPASS:-$PI_PASSWORD}"
  SSH_BASE=(sshpass -e "${SSH_BASE[@]}")
  SCP_BASE=(sshpass -e "${SCP_BASE[@]}")
  pass "using sshpass from SSHPASS/PI_PASSWORD without interactive prompt"
else
  SSH_BASE+=( -o BatchMode=yes )
  SCP_BASE+=( -o BatchMode=yes )
  warn "no SSHPASS/PI_PASSWORD found; using SSH key auth only with BatchMode=yes"
fi

"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "test -d '$PI_REPO'"
pass "Pi repo reachable without interactive prompt"

python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_picker_d3k.py
pass "local python compile passed"
if command -v node >/dev/null 2>&1; then node --check web/app.js >/dev/null && pass "local node check passed"; else warn "node not available locally"; fi

tarball="/tmp/pi_p25_v0_4d3k_${STAMP}.tgz"
tar -czf "$tarball" src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_picker_d3k.py web/app.js
"${SCP_BASE[@]}" "$tarball" "${PI_USER}@${PI_HOST}:/tmp/"
pass "copied patch tarball to Pi"

remote_script="
set -Eeuo pipefail
cd '$PI_REPO'
mkdir -p runtime/patch_backups/deploy_v0_4d3k_$STAMP
cp -f src/pi_p25_scanner/backend.py runtime/patch_backups/deploy_v0_4d3k_$STAMP/backend.py.bak || true
cp -f src/pi_p25_scanner/radioreference_picker_d3k.py runtime/patch_backups/deploy_v0_4d3k_$STAMP/radioreference_picker_d3k.py.bak || true
cp -f web/app.js runtime/patch_backups/deploy_v0_4d3k_$STAMP/app.js.bak || true
tar -xzf /tmp/pi_p25_v0_4d3k_$STAMP.tgz
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_picker_d3k.py
if command -v node >/dev/null 2>&1; then node --check web/app.js >/dev/null; fi
sudo systemctl restart pi-p25-scanner.service
sleep 2
curl -fsS http://127.0.0.1:8070/api/status >/tmp/pi_p25_status_v0_4d3k.json
python3 - <<'PYREMOTE'
import json, urllib.request
payload = {"state":"AZ","county":"Maricopa","city":"Mesa"}
req = urllib.request.Request('http://127.0.0.1:8070/api/radioreference/systems', data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=45) as response:
    body = json.loads(response.read().decode())
print(json.dumps({k: body.get(k) for k in ('ok','picker_parser','state_id','county_id','system_count','state_candidate_count','county_candidate_count')}, indent=2, sort_keys=True))
if body.get('picker_parser') != 'forced-explicit-soap-v0.4d3k':
    raise SystemExit('D3K picker parser did not take over')
PYREMOTE
rm -f /tmp/pi_p25_v0_4d3k_$STAMP.tgz
"
"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "$remote_script"
pass "remote deploy/restart/parser probe passed"
rm -f "$tarball"
pass "deploy complete"
