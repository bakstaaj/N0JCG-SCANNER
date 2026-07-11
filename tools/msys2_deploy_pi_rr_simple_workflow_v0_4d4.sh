#!/usr/bin/env bash
set -Eeuo pipefail

PATCH_NAME="deploy_v0_4d4_rr_simple_workflow"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/${PATCH_NAME}_${STAMP}.txt"
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
exec > >(tee -a "$LOG_FILE") 2>&1
pass(){ PASS_COUNT=$((PASS_COUNT+1)); echo "PASS: $*"; }
warn(){ WARN_COUNT=$((WARN_COUNT+1)); echo "WARN: $*"; }
fail(){ FAIL_COUNT=$((FAIL_COUNT+1)); echo "FAIL: $*"; echo "UPLOAD_FILE_MSYS=$LOG_FILE"; echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"; echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; echo "FINAL: FAIL"; exit 1; }
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; fi' ERR

echo "=== Deploy V0.4D4 RadioReference simple workflow UI ==="

find_repo_root(){
  local d="$PWD"
  while [[ "$d" != "/" ]]; do
    if [[ -f "$d/src/pi_p25_scanner/backend.py" && -f "$d/web/app.js" ]]; then printf '%s\n' "$d"; return 0; fi
    d="$(dirname "$d")"
  done
  return 1
}
REPO_ROOT="$(find_repo_root)" || fail "run from PI-P25-SCANNER repo"
cd "$REPO_ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  pass "loaded .env"
else
  warn ".env not found; using shell environment only"
fi

PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
pass "target fixed to ${PI_USER}@${PI_HOST}:${PI_REPO}"

SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
SCP_BASE=(scp -O -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
if [[ -n "${SSHPASS:-}" && $(command -v sshpass || true) ]]; then
  SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
  SCP_BASE=(sshpass -e scp -O -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
  pass "using sshpass with SSHPASS"
elif [[ -n "${PI_PASSWORD:-}" && $(command -v sshpass || true) ]]; then
  export SSHPASS="$PI_PASSWORD"
  SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
  SCP_BASE=(sshpass -e scp -O -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
  pass "using sshpass with PI_PASSWORD"
else
  SSH_BASE+=( -o BatchMode=yes )
  SCP_BASE+=( -o BatchMode=yes )
  warn "no SSHPASS/PI_PASSWORD available; trying SSH key auth only without prompting"
fi

"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "test -d '$PI_REPO'" || fail "Pi repo not reachable non-interactively"
pass "Pi repo reachable without interactive prompt"

grep -q "V0.4D4 RR SIMPLE WORKFLOW UI" web/app.js || fail "local app.js missing V0.4D4 marker"
TMP_TAR="/tmp/pi_p25_v0_4d4_rr_simple_workflow_${STAMP}.tar.gz"
tar -czf "$TMP_TAR" web/app.js
pass "built deploy tar"
"${SCP_BASE[@]}" "$TMP_TAR" "${PI_USER}@${PI_HOST}:/tmp/"
pass "copied deploy tar to Pi"

REMOTE_TAR="/tmp/$(basename "$TMP_TAR")"
"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "bash -s" <<REMOTE
set -Eeuo pipefail
cd '$PI_REPO'
STAMP='$STAMP'
mkdir -p runtime/patch_backups/deploy_v0_4d4_rr_simple_workflow_\$STAMP
cp -p web/app.js runtime/patch_backups/deploy_v0_4d4_rr_simple_workflow_\$STAMP/app.js.bak 2>/dev/null || true
tar -xzf '$REMOTE_TAR' -C '$PI_REPO'
grep -q 'V0.4D4 RR SIMPLE WORKFLOW UI' web/app.js
if command -v node >/dev/null 2>&1; then node --check web/app.js; fi
if command -v git >/dev/null 2>&1; then git diff --check -- web/app.js; fi
sudo systemctl restart pi-p25-scanner.service
sleep 2
python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8070/api/status', timeout=5) as r:
    payload=json.loads(r.read().decode())
print('STATUS_OK', payload.get('ok'), payload.get('scanner_state'))
PY
REMOTE
pass "deployed app.js, restarted backend, probed status"

rm -f "$TMP_TAR" 2>/dev/null || true
echo "UPLOAD_FILE_MSYS=$LOG_FILE"
echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"
echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
echo "FINAL: PASS"
