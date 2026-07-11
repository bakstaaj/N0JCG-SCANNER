#!/usr/bin/env bash
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="${LOG_DIR}/deploy_named_config_controls_v0_4g4_${STAMP}.txt"
mkdir -p "$LOG_DIR"
exec > >(tee "$LOG_FILE") 2>&1
pass() { echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
finish() {
  echo "UPLOAD_FILE_MSYS=$LOG_FILE"
  echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"
  echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then echo "FINAL: PASS"; else echo "FINAL: FAIL"; fi
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line ${LINENO} rc=$rc"; fi; finish; exit $rc' EXIT

if [[ ! -d .git ]]; then
  fail "run this from the PI-P25-SCANNER repo root"
  exit 1
fi
pass "repo root detected"

if [[ -f tools/msys2_env_common.sh ]]; then
  # shellcheck disable=SC1091
  source tools/msys2_env_common.sh || true
  if declare -F p25_load_dotenv >/dev/null 2>&1; then p25_load_dotenv || true; fi
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-PI-SDR}"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
PI_PASSWORD="${PI_PASSWORD:-${SSHPASS:-}}"
REMOTE_TAR="/tmp/pi_p25_v0_4g4_named_configs_${STAMP}.tar.gz"
LOCAL_TAR="/tmp/pi_p25_v0_4g4_named_configs_${STAMP}.tar.gz"

SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "${PI_USER}@${PI_HOST}")
SCP_BASE=(scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [[ -n "$PI_PASSWORD" ]]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    fail "PI_PASSWORD/SSHPASS is set but sshpass is not installed in MSYS2"
    exit 1
  fi
  SSH_BASE=(sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "${PI_USER}@${PI_HOST}")
  SCP_BASE=(sshpass -p "$PI_PASSWORD" scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
fi

for f in src/pi_p25_scanner/config_store.py src/pi_p25_scanner/backend.py web/app.js web/app.css; do
  [[ -f "$f" ]] || { fail "missing deploy file: $f"; exit 1; }
done
pass "deploy files present"

tar -czf "$LOCAL_TAR" src/pi_p25_scanner/config_store.py src/pi_p25_scanner/backend.py web/app.js web/app.css
pass "created deploy tarball: $LOCAL_TAR"

"${SCP_BASE[@]}" "$LOCAL_TAR" "${PI_USER}@${PI_HOST}:${REMOTE_TAR}"
pass "copied deploy tarball to ${PI_HOST}:${REMOTE_TAR}"

"${SSH_BASE[@]}" "REMOTE_TAR='$REMOTE_TAR' PI_REPO='$PI_REPO' bash -s" <<'REMOTE'
set -Eeuo pipefail
cd "$PI_REPO"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="runtime/patch_backups/v0_4g4_named_configs_remote_${STAMP}"
mkdir -p "$BACKUP_DIR"
cp -p src/pi_p25_scanner/config_store.py "$BACKUP_DIR/config_store.py" 2>/dev/null || true
cp -p src/pi_p25_scanner/backend.py "$BACKUP_DIR/backend.py" 2>/dev/null || true
cp -p web/app.js "$BACKUP_DIR/app.js" 2>/dev/null || true
cp -p web/app.css "$BACKUP_DIR/app.css" 2>/dev/null || true
mkdir -p runtime/settings/configs

tar -xzf "$REMOTE_TAR" -C "$PI_REPO"
python3 -m py_compile src/pi_p25_scanner/config_store.py src/pi_p25_scanner/backend.py
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
else
  grep -q 'NAMED_CONFIG_DYNAMIC_PANEL_V0_4G3' web/app.js
fi
sudo systemctl restart pi-p25-scanner.service
for i in $(seq 1 45); do
  if python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8070/api/status', timeout=1.0) as r:
    data = json.load(r)
assert isinstance(data, dict)
PY
  then
    break
  fi
  sleep 1
  if [[ "$i" -eq 45 ]]; then
    echo "SERVICE_STATUS_BEGIN"
    sudo systemctl --no-pager status pi-p25-scanner.service || true
    echo "JOURNAL_BEGIN"
    sudo journalctl -u pi-p25-scanner.service -n 80 --no-pager || true
    exit 1
  fi
done
python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8070/api/config/named', timeout=2.0) as r:
    data = json.load(r)
assert data.get('ok') is True, data
with urllib.request.urlopen('http://127.0.0.1:8070/app.js', timeout=2.0) as r:
    text = r.read().decode('utf-8')
assert 'NAMED_CONFIG_DYNAMIC_PANEL_V0_4G3' in text
assert 'namedConfigPanel' in text
print('REMOTE_PROBE_PASS named config API and dynamic UI marker')
PY
rm -f "$REMOTE_TAR"
REMOTE
pass "remote named-config API and dynamic UI probe passed"

echo "Dashboard: http://${PI_HOST}:8070"
pass "deploy complete"
