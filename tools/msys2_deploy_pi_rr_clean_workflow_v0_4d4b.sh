#!/usr/bin/env bash
set -Eeuo pipefail
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
LOG_FILE="$LOG_DIR/deploy_v0_4d4b_rr_clean_workflow_${STAMP}.txt"
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
mkdir -p "$LOG_DIR" 2>/dev/null || true
exec > >(tee -a "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
finish(){ local rc="$1"; echo "UPLOAD_FILE_MSYS=$LOG_FILE"; echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"; echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; if [[ "$rc" -eq 0 && "$FAIL_COUNT" -eq 0 ]]; then echo "FINAL: PASS"; else echo "FINAL: FAIL"; fi; }
trap 'rc=$?; fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish $rc; exit $rc' ERR

echo "=== Deploy V0.4D4B clean RR workflow UI ==="
ROOT="$(pwd)"
[[ -f "$ROOT/web/app.js" && -f "$ROOT/web/app.css" ]] || { fail "run from repo root with web/app.js and web/app.css"; finish 1; exit 1; }

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  pass "loaded .env"
fi
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
pass "target fixed to ${PI_USER}@${PI_HOST}:${PI_REPO}"

SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
SCP_BASE=(scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
if [[ -n "${SSHPASS:-}" || -n "${PI_PASSWORD:-}" ]]; then
  command -v sshpass >/dev/null 2>&1 || { fail "sshpass required when SSHPASS/PI_PASSWORD is used"; finish 1; exit 1; }
  export SSHPASS="${SSHPASS:-${PI_PASSWORD:-}}"
  SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
  SCP_BASE=(sshpass -e scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
  pass "using sshpass with stored password"
else
  SSH_BASE+=( -o BatchMode=yes )
  SCP_BASE+=( -o BatchMode=yes )
  warn "no SSHPASS/PI_PASSWORD found; trying SSH key auth only"
fi

"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "test -d '$PI_REPO'"
pass "Pi repo reachable without interactive prompt"

tmp="runtime/deploy_v0_4d4b_rr_clean_workflow_${STAMP}.tar.gz"
mkdir -p runtime
tar -czf "$tmp" web/app.js web/app.css
pass "built deploy tar"
"${SCP_BASE[@]}" "$tmp" "${PI_USER}@${PI_HOST}:/tmp/pi_p25_d4b_rr_clean_workflow.tar.gz"
pass "copied deploy tar to Pi"

"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "cd '$PI_REPO' && \
  mkdir -p runtime/patch_backups/deploy_v0_4d4b_${STAMP} && \
  cp -p web/app.js web/app.css runtime/patch_backups/deploy_v0_4d4b_${STAMP}/ 2>/dev/null || true && \
  tar -xzf /tmp/pi_p25_d4b_rr_clean_workflow.tar.gz && \
  python3 - <<'PY'
from pathlib import Path
for p in [Path('web/app.js'), Path('web/app.css')]:
    text = p.read_text(encoding='utf-8').replace('\\r\\n','\\n').replace('\\r','\\n')
    lines = [line.rstrip() for line in text.split('\\n')]
    while lines and lines[-1] == '':
        lines.pop()
    p.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')
PY
  if command -v node >/dev/null 2>&1; then node --check web/app.js; fi && \
  grep -q 'RR_CLEAN_WORKFLOW_V0_4D4B' web/app.js && \
  grep -q 'RR_CLEAN_WORKFLOW_V0_4D4B' web/app.css && \
  sudo systemctl restart pi-p25-scanner.service && \
  sleep 2"
pass "deployed clean RR workflow UI and restarted backend"

"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "python3 - <<'PY'
import json, urllib.request
base='http://127.0.0.1:8070'
status=json.loads(urllib.request.urlopen(base+'/api/status', timeout=5).read().decode())
rr=json.loads(urllib.request.urlopen(base+'/api/radioreference/status', timeout=8).read().decode())
print('STATUS_OK', status.get('ok'), status.get('scanner_state'))
print('RR_CONFIGURED', rr.get('configured'), rr.get('username'), rr.get('zeep',{}).get('available'))
PY"
pass "probed backend and RadioReference status"

finish 0
