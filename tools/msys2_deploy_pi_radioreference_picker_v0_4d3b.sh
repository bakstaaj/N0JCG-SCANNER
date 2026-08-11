#!/usr/bin/env bash
set -Eeuo pipefail
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/deploy_v0_4d3b_radioreference_picker_${STAMP}.txt"
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
log(){ printf '%s\n' "$*" | tee -a "$LOG_FILE"; }
pass(){ PASS_COUNT=$((PASS_COUNT+1)); log "PASS: $*"; }
warn(){ WARN_COUNT=$((WARN_COUNT+1)); log "WARN: $*"; }
fail(){ FAIL_COUNT=$((FAIL_COUNT+1)); log "FAIL: $*"; }
finish(){ local final=PASS; [[ "$FAIL_COUNT" -gt 0 ]] && final=FAIL; log "UPLOAD_FILE_MSYS=$LOG_FILE"; log "UPLOAD_FILE_WINDOWS=$(printf '%s' "$LOG_FILE" | sed 's#^/c#C:#; s#/#\\\\#g')"; log "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; log "FINAL: $final"; [[ "$final" == PASS ]]; }
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish || true; exit $rc; fi' ERR

PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [[ -n "${PI_PASSWORD:-${SSHPASS:-}}" ]] && command -v sshpass >/dev/null 2>&1; then
  SSHPASS_VALUE="${PI_PASSWORD:-${SSHPASS:-}}"
  SSH=(sshpass -p "$SSHPASS_VALUE" ssh "${SSH_OPTS[@]}")
  SCP=(sshpass -p "$SSHPASS_VALUE" scp -O "${SSH_OPTS[@]}")
else
  SSH=(ssh "${SSH_OPTS[@]}")
  SCP=(scp -O "${SSH_OPTS[@]}")
fi
TARGET="${PI_USER}@${PI_HOST}"
log "=== Deploy V0.4D3B RadioReference picker runtime recovery ==="
log "TARGET=$TARGET"
log "PI_REPO=$PI_REPO"

[[ -f src/pi_p25_scanner/backend.py && -f src/pi_p25_scanner/radioreference_import.py && -f web/app.js ]] || { fail "run from repo root"; finish || true; exit 1; }
pass "local files present"
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_import.py
pass "local python syntax valid"
if command -v node >/dev/null 2>&1; then node --check web/app.js; pass "local app.js syntax valid"; else warn "node not found locally; skipped app.js check"; fi
TMP_TAR="/tmp/pi_p25_v0_4d3b_rr_picker_${STAMP}.tar"
tar -cf "$TMP_TAR" src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_import.py web/app.js
pass "created deploy tar"
"${SSH[@]}" "$TARGET" "test -d '$PI_REPO'"
pass "remote repo exists"
"${SCP[@]}" "$TMP_TAR" "$TARGET:/tmp/pi_p25_v0_4d3b_rr_picker.tar"
pass "uploaded deploy tar to $PI_HOST"
"${SSH[@]}" "$TARGET" "cd '$PI_REPO' && mkdir -p runtime/patch_backups/deploy_v0_4d3b_${STAMP} && cp src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_import.py web/app.js runtime/patch_backups/deploy_v0_4d3b_${STAMP}/ && tar -xf /tmp/pi_p25_v0_4d3b_rr_picker.tar && python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_import.py && if command -v node >/dev/null 2>&1; then node --check web/app.js; fi"
pass "remote files deployed and validated"
"${SSH[@]}" "$TARGET" "sudo systemctl restart pi-p25-scanner.service && sleep 2 && systemctl is-active --quiet pi-p25-scanner.service"
pass "backend service restarted"
"${SSH[@]}" "$TARGET" "python3 - <<'PY'
import json, urllib.request
for url in ('http://127.0.0.1:8070/api/status', 'http://127.0.0.1:8070/api/radioreference/status'):
    with urllib.request.urlopen(url, timeout=5) as r:
        payload=json.loads(r.read().decode())
    print(url, payload.get('ok'), payload.get('scanner_state', payload.get('configured')))
PY"
pass "status endpoints responded"
finish
