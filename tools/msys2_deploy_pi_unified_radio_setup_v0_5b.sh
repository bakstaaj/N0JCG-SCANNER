#!/usr/bin/env bash
set -Eeuo pipefail
PATCH_NAME="deploy_v0_5b_unified_radio_setup"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || LOG_DIR="$PWD"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/${PATCH_NAME}_${STAMP}.txt"
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
exec > >(tee -a "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
finish(){
  echo "UPLOAD_FILE_MSYS=$LOG_FILE"
  case "$LOG_FILE" in /c/*) echo "UPLOAD_FILE_WINDOWS=$(echo "$LOG_FILE" | sed -E 's#^/c/#C:\\\\#; s#/#\\\\#g')" ;; esac
  echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then echo "FINAL: PASS"; else echo "FINAL: FAIL"; fi
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted at line $LINENO rc=$rc"; fi; finish; exit $rc' EXIT

echo "=== Deploy V0.5B unified Radio Setup workflow ==="
[[ -f web/app.js ]] || { fail "run from repo root; web/app.js not found"; exit 1; }

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env || true
  set +a
  pass "loaded .env"
else
  warn ".env not found; using existing environment or SSH key"
fi

PI_HOST="192.168.254.63"
PI_USER="${PI_USER:-pi}"
REMOTE_ROOT="/home/pi/PI-P25-SCANNER"
pass "target fixed to ${PI_USER}@${PI_HOST}:${REMOTE_ROOT}"

SSH_BASE=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
SCP_BASE=(-O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
if [[ -n "${SSHPASS:-}" || -n "${PI_PASSWORD:-}" ]]; then
  export SSHPASS="${SSHPASS:-$PI_PASSWORD}"
  SSH=(sshpass -e ssh "${SSH_BASE[@]}")
  SCP=(sshpass -e scp "${SCP_BASE[@]}")
  pass "using sshpass with PI_PASSWORD/SSHPASS"
else
  SSH=(ssh "${SSH_BASE[@]}" -o BatchMode=yes)
  SCP=(scp "${SCP_BASE[@]}" -o BatchMode=yes)
  warn "PI_PASSWORD/SSHPASS not set; trying SSH key auth only"
fi

"${SSH[@]}" "${PI_USER}@${PI_HOST}" "test -d '${REMOTE_ROOT}' && test -w '${REMOTE_ROOT}/web'"
pass "Pi repo reachable without interactive prompt"

TMP_TAR="/tmp/pi_p25_v0_5b_unified_radio_setup_${STAMP}.tar.gz"
tar -czf "$TMP_TAR" web/app.js
pass "built deploy tar"
"${SCP[@]}" "$TMP_TAR" "${PI_USER}@${PI_HOST}:/tmp/"
pass "copied deploy tar to Pi"
"${SSH[@]}" "${PI_USER}@${PI_HOST}" "set -Eeuo pipefail
cd '${REMOTE_ROOT}'
backup_dir='runtime/patch_backups/v0_5b_unified_radio_setup_${STAMP}'
mkdir -p \"\$backup_dir\"
cp -p web/app.js \"\$backup_dir/app.js\"
tar -xzf '/tmp/$(basename "$TMP_TAR")'
python3 - <<'PYREMOTE'
from pathlib import Path
text = Path('web/app.js').read_text(encoding='utf-8')
if 'V0.5B_RADIO_SETUP_UNIFIED' not in text:
    raise SystemExit('V0.5B marker missing after deploy')
print('REMOTE_JS_MARKER_OK')
PYREMOTE
"
pass "deployed app.js and verified marker on Pi"

if curl -fsS "http://${PI_HOST}:8070/app.js" | grep -q 'V0.5B_RADIO_SETUP_UNIFIED'; then
  pass "web server is serving V0.5B app.js marker"
else
  warn "could not verify marker from web server; hard-refresh browser and retry if UI looks old"
fi

echo "Open http://${PI_HOST}:8070 and hard-refresh. Use the Radio Setup button/menu item."
