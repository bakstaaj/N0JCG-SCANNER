#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_NAME="deploy_v0_4d2_radioreference_credentials_login"
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
  log "UPLOAD_FILE_WINDOWS=$(printf '%s' "$LOG_FILE" | sed 's#^/c/#C:\\\\#; s#/#\\\\#g')"
  log "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then log "FINAL: PASS"; else log "FINAL: FAIL"; fi
}
trap 'rc=$?; fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish; exit $rc' ERR
trap 'finish' EXIT

log "=== Deploy V0.4D2 RadioReference credential/login fix ==="
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
pass "using fixed Pi target ${PI_USER}@${PI_HOST}:${PI_REPO}"

if [[ -f "src/pi_p25_scanner/radioreference_import.py" && -f "web/app.js" ]]; then
  REPO_ROOT="$PWD"
elif [[ -f "$HOME/sdrdev/PI-P25-SCANNER/src/pi_p25_scanner/radioreference_import.py" ]]; then
  REPO_ROOT="$HOME/sdrdev/PI-P25-SCANNER"
else
  fail "could not locate PI-P25-SCANNER repo root"
  exit 1
fi
cd "$REPO_ROOT"
pass "repo root detected: $REPO_ROOT"

python3 -m py_compile src/pi_p25_scanner/radioreference_import.py
pass "local py_compile passed"
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
  pass "local node check passed"
else
  warn "node not found locally; skipped app.js check"
fi

SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
SCP_BASE=(scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [[ -n "${PI_PASSWORD:-${SSHPASS:-}}" ]] && command -v sshpass >/dev/null 2>&1; then
  export SSHPASS="${PI_PASSWORD:-${SSHPASS:-}}"
  SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  SCP_BASE=(sshpass -e scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  pass "sshpass enabled"
else
  warn "sshpass not enabled; using existing SSH auth"
fi

REMOTE="${PI_USER}@${PI_HOST}"
"${SSH_BASE[@]}" "$REMOTE" "test -d '$PI_REPO'"
pass "remote repo exists"

TMP_TAR="/tmp/pi_p25_v0_4d2_rr_${STAMP}.tar"
tar -cf "$TMP_TAR" src/pi_p25_scanner/radioreference_import.py web/app.js
"${SCP_BASE[@]}" "$TMP_TAR" "$REMOTE:/tmp/pi_p25_v0_4d2_rr.tar"
rm -f "$TMP_TAR"
pass "copied patched files to Pi"

"${SSH_BASE[@]}" "$REMOTE" "cd '$PI_REPO' && \
  mkdir -p runtime/patch_backups/deploy_v0_4d2_${STAMP} && \
  cp -p src/pi_p25_scanner/radioreference_import.py runtime/patch_backups/deploy_v0_4d2_${STAMP}/radioreference_import.py.bak && \
  cp -p web/app.js runtime/patch_backups/deploy_v0_4d2_${STAMP}/app.js.bak && \
  tar -xf /tmp/pi_p25_v0_4d2_rr.tar -C '$PI_REPO' && \
  rm -f /tmp/pi_p25_v0_4d2_rr.tar && \
  python3 -m py_compile src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py && \
  if command -v node >/dev/null 2>&1; then node --check web/app.js; fi && \
  sudo systemctl restart pi-p25-scanner.service && \
  sleep 2 && \
  systemctl is-active --quiet pi-p25-scanner.service"
pass "remote compile and service restart passed"

"${SSH_BASE[@]}" "$REMOTE" "python3 - <<'PY'
import json, urllib.request
base='http://127.0.0.1:8070'
for path in ['/api/status', '/api/radioreference/status']:
    with urllib.request.urlopen(base+path, timeout=5) as response:
        payload=json.loads(response.read().decode('utf-8','replace'))
    print(path, json.dumps({
        'ok': payload.get('ok'),
        'scanner_state': payload.get('scanner_state'),
        'configured': payload.get('configured'),
        'username': payload.get('username'),
        'app_key_configured': payload.get('app_key_configured'),
        'password_configured': payload.get('password_configured'),
        'credential_save_mode': payload.get('credential_save_mode'),
        'soap_auth_mode': payload.get('soap_auth_mode'),
        'zeep': payload.get('zeep'),
    }, sort_keys=True))
PY"
pass "remote API probes passed"

if "${SSH_BASE[@]}" "$REMOTE" "python3 - <<'PY'
from pathlib import Path
p=Path('/home/pi/PI-P25-SCANNER/runtime/settings/radioreference.env')
print('credentials_file_exists', p.exists())
if p.exists():
    text=p.read_text(encoding='utf-8', errors='replace')
    for key in ('RADIOREFERENCE_APP_KEY','RADIOREFERENCE_USERNAME','RADIOREFERENCE_PASSWORD'):
        print(key, 'present' if (key+'=') in text and text.split(key+'=',1)[1].split('\n',1)[0].strip() else 'missing_or_blank')
PY"; then
  pass "remote credential file sanity probe completed"
else
  warn "credential file sanity probe failed"
fi
