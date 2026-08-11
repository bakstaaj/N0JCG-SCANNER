#!/usr/bin/env bash
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/deploy_v0_4d3h_radioreference_picker_${STAMP}.txt"
mkdir -p "$LOG_DIR" 2>/dev/null || true
exec > >(tee -a "$LOG_FILE") 2>&1

pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); echo "UPLOAD_FILE_MSYS=$LOG_FILE"; echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"; echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; echo "FINAL: FAIL"; exit 1; }
finish(){ echo "UPLOAD_FILE_MSYS=$LOG_FILE"; echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"; echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; echo "FINAL: PASS"; }
trap 'fail "deploy aborted unexpectedly at line $LINENO rc=$?"' ERR

echo "=== Deploy V0.4D3H RadioReference picker non-interactive ==="

if [[ -d .git && -f src/pi_p25_scanner/backend.py ]]; then
  REPO_ROOT="$PWD"
elif [[ -d "$HOME/sdrdev/scanner/.git" ]]; then
  REPO_ROOT="$HOME/sdrdev/scanner"
  cd "$REPO_ROOT"
else
  fail "repo root not found; run from ~/sdrdev/scanner"
fi
pass "repo root detected: $REPO_ROOT"

if [[ -f .env ]]; then
  set +u
  # shellcheck disable=SC1091
  source .env || warn ".env exists but could not be sourced cleanly"
  set -u
  pass "loaded .env for credentials, ignoring any PI_HOST from it"
else
  warn ".env not found; using shell environment only"
fi

PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"

# Non-interactive auth policy:
# 1. Use SSHPASS if already exported.
# 2. Else use PI_PASSWORD if already exported or loaded from .env.
# 3. Else try SSH key only with BatchMode=yes.
# Never prompt interactively.
USE_SSHPASS=0
if [[ -n "${SSHPASS:-}" ]]; then
  USE_SSHPASS=1
  pass "using SSHPASS from environment/.env"
elif [[ -n "${PI_PASSWORD:-}" ]]; then
  export SSHPASS="$PI_PASSWORD"
  USE_SSHPASS=1
  pass "using PI_PASSWORD from environment/.env via sshpass"
else
  warn "SSHPASS/PI_PASSWORD not set; trying SSH key auth only without prompting"
fi

if (( USE_SSHPASS )); then
  command -v sshpass >/dev/null 2>&1 || fail "sshpass is required when PI_PASSWORD/SSHPASS is set; install it in MSYS2 or remove password env to use key auth"
  SSH_CMD=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  SCP_CMD=(sshpass -e scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
else
  SSH_CMD=(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  SCP_CMD=(scp -O -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
fi

pass "target: ${PI_USER}@${PI_HOST}:${PI_REPO}"

if ! "${SSH_CMD[@]}" "${PI_USER}@${PI_HOST}" "test -d '$PI_REPO' && test -w '$PI_REPO'"; then
  fail "non-interactive SSH failed. Set PI_PASSWORD or SSHPASS in .env/shell, or configure SSH key auth for ${PI_USER}@${PI_HOST}. No interactive password prompt is used by this helper."
fi
pass "non-interactive SSH connection verified"

TOUCH_FILES=(
  src/pi_p25_scanner/radioreference_import.py
  src/pi_p25_scanner/backend.py
  web/index.html
  web/app.js
)
for f in "${TOUCH_FILES[@]}"; do
  [[ -f "$f" ]] || fail "required local file missing: $f"
done
pass "required local files present"

python3 -m py_compile src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py
pass "local Python compile passed"
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
  pass "local node --check web/app.js passed"
else
  warn "node not available locally; skipped app.js syntax check"
fi

git --no-pager diff --check -- src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py web/index.html web/app.js
pass "local git diff --check passed"

TMP_TAR="/tmp/pi_p25_v0_4d3h_rr_picker_${STAMP}.tar"
tar -cf "$TMP_TAR" "${TOUCH_FILES[@]}"
"${SCP_CMD[@]}" "$TMP_TAR" "${PI_USER}@${PI_HOST}:/tmp/pi_p25_v0_4d3h_rr_picker.tar"
pass "copied deploy tar to Pi"

"${SSH_CMD[@]}" "${PI_USER}@${PI_HOST}" "bash -s" <<'REMOTE'
set -Eeuo pipefail
cd /home/pi/n0jcg-scanner
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="runtime/patch_backups/deploy_v0_4d3h_radioreference_picker_${STAMP}"
mkdir -p "$BACKUP_DIR"
for f in src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py web/index.html web/app.js; do
  if [[ -f "$f" ]]; then
    mkdir -p "$BACKUP_DIR/$(dirname "$f")"
    cp -a "$f" "$BACKUP_DIR/$f"
  fi
done
# Preserve local Pi secrets. Only code/UI files are deployed.
tar -xf /tmp/pi_p25_v0_4d3h_rr_picker.tar
python3 -m py_compile src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py
if command -v node >/dev/null 2>&1; then node --check web/app.js; fi
sudo systemctl restart pi-p25-scanner.service
sleep 2
systemctl is-active --quiet pi-p25-scanner.service
curl -fsS --max-time 5 http://127.0.0.1:8070/api/status >/tmp/pi_p25_status_v0_4d3h.json
REMOTE
pass "remote deploy, compile, restart, and status probe passed"

STATUS_CODE="$(curl -sS --max-time 15 -o /tmp/pi_p25_rr_status_v0_4d3h.json -w '%{http_code}' "http://${PI_HOST}:8070/api/radioreference/status" || true)"
echo "===== /api/radioreference/status HTTP ${STATUS_CODE} ====="
cat /tmp/pi_p25_rr_status_v0_4d3h.json 2>/dev/null || true
echo
echo "===== END /api/radioreference/status ====="
[[ "$STATUS_CODE" == "200" ]] || fail "RadioReference status endpoint did not return HTTP 200"
pass "RadioReference status endpoint returned HTTP 200"

SYSTEMS_BODY="$(mktemp)"
SYSTEMS_CODE="$(curl -sS --max-time 45 -o "$SYSTEMS_BODY" -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  -d '{"state":"AZ","county":"Maricopa","city":"Mesa"}' \
  "http://${PI_HOST}:8070/api/radioreference/systems" || true)"
echo "===== /api/radioreference/systems HTTP ${SYSTEMS_CODE} ====="
cat "$SYSTEMS_BODY" || true
echo
echo "===== END /api/radioreference/systems ====="
case "$SYSTEMS_CODE" in
  200|202) pass "RadioReference systems endpoint returned HTTP ${SYSTEMS_CODE}" ;;
  400) warn "RadioReference systems endpoint returned HTTP 400; deploy is OK but parser/auth details above need review" ;;
  *) fail "RadioReference systems endpoint unexpected HTTP ${SYSTEMS_CODE}" ;;
esac

finish
