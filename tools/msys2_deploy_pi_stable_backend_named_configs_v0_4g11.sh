#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="deploy_v0_4g11_stable_backend_named_configs"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/${SCRIPT_NAME}_${STAMP}.txt"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
exec > >(tee -a "$LOG_FILE") 2>&1
trap 'rc=$?; if [[ $rc -ne 0 ]]; then FAIL_COUNT=$((FAIL_COUNT+1)); echo "FAIL: deploy aborted unexpectedly at line ${LINENO} rc=$rc"; finish; fi' ERR
finish() {
  echo "UPLOAD_FILE_MSYS=$LOG_FILE"
  win_path="$LOG_FILE"
  win_path="${win_path#/c/}"
  win_path="C:\\${win_path//\//\\}"
  echo "UPLOAD_FILE_WINDOWS=$win_path"
  echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [[ $FAIL_COUNT -eq 0 ]]; then echo "FINAL: PASS"; else echo "FINAL: FAIL"; fi
}
pass() { echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn() { echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail() { echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); finish; exit 1; }

[[ -f src/pi_p25_scanner/backend.py && -f src/pi_p25_scanner/config_store.py && -d web ]] || fail "run from PI-P25-SCANNER repo root"
pass "repo root detected"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
PI_HOST="${PI_HOST:-192.168.254.63}"
PI_USER="${PI_USER:-pi}"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
PI_PASSWORD="${PI_PASSWORD:-${SSHPASS:-}}"
[[ -n "$PI_PASSWORD" ]] || fail "PI_PASSWORD or SSHPASS is required in .env or environment"
export SSHPASS="$PI_PASSWORD"
pass "using Pi target ${PI_USER}@${PI_HOST}:${PI_REPO}"

for cmd in sshpass scp ssh tar python3; do
  command -v "$cmd" >/dev/null 2>&1 || fail "missing required command: $cmd"
done
pass "deploy prerequisites present"

python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/config_store.py
pass "local python compile passed"

TARBALL="/tmp/pi_p25_v0_4g11_stable_backend_${STAMP}.tar.gz"
tar -czf "$TARBALL" src/pi_p25_scanner/backend.py src/pi_p25_scanner/config_store.py
pass "created deploy tarball: $TARBALL"

SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/c/Users/jim/.ssh/known_hosts)
SCP_BASE=(sshpass -e scp -O -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/c/Users/jim/.ssh/known_hosts)
"${SCP_BASE[@]}" "$TARBALL" "${PI_USER}@${PI_HOST}:/tmp/$(basename "$TARBALL")"
pass "copied deploy tarball to ${PI_HOST}"

REMOTE_TARBALL="/tmp/$(basename "$TARBALL")"
"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "PI_REPO='$PI_REPO' REMOTE_TARBALL='$REMOTE_TARBALL' bash -s" <<'REMOTE'
set -Eeuo pipefail
cd "$PI_REPO"
mkdir -p "runtime/settings/backups/deploy_v0_4g11"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
cp -f src/pi_p25_scanner/backend.py "runtime/settings/backups/deploy_v0_4g11/backend.py.${stamp}.bak" 2>/dev/null || true
cp -f src/pi_p25_scanner/config_store.py "runtime/settings/backups/deploy_v0_4g11/config_store.py.${stamp}.bak" 2>/dev/null || true
tar -xzf "$REMOTE_TARBALL" -C "$PI_REPO"
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/config_store.py
mkdir -p runtime/settings/configs
if systemctl list-unit-files | grep -q '^pi-p25-scanner.service'; then
  sudo systemctl restart pi-p25-scanner.service
else
  pkill -f 'pi_p25_scanner.backend' 2>/dev/null || true
  nohup python3 -m pi_p25_scanner.backend --host 0.0.0.0 --port 8070 >/tmp/pi-p25-scanner-backend.log 2>&1 &
fi
REMOTE
pass "remote files installed, compiled, and service restart requested"

probe_remote() {
  local path="$1"
  "${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "python3 - '$path'" <<'PY'
import json, sys, time, urllib.request
path = sys.argv[1]
url = 'http://127.0.0.1:8070' + path
last = ''
for _ in range(45):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            data = r.read().decode('utf-8', 'replace')
        payload = json.loads(data)
        print('PROBE_OK', url, 'ok=', payload.get('ok'))
        sys.exit(0)
    except Exception as exc:
        last = repr(exc)
        time.sleep(1)
print('PROBE_FAIL', url, last)
sys.exit(1)
PY
}

if ! probe_remote /api/status; then
  warn "/api/status probe failed; collecting service diagnostics"
  "${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "bash -s" <<'REMOTE_DIAG' || true
echo SERVICE_STATUS_BEGIN
systemctl status pi-p25-scanner.service --no-pager -l || true
echo JOURNAL_BEGIN
journalctl -u pi-p25-scanner.service --no-pager -n 120 || true
echo PORTS_BEGIN
ss -ltnp 2>/dev/null | grep ':8070' || true
REMOTE_DIAG
  fail "/api/status did not respond after stable backend deploy"
fi
pass "remote /api/status responded"

if ! probe_remote /api/config/named; then
  warn "/api/config/named probe failed; collecting service diagnostics"
  "${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "journalctl -u pi-p25-scanner.service --no-pager -n 120" || true
  fail "/api/config/named did not respond after stable backend deploy"
fi
pass "remote /api/config/named responded"

LAN_IP="$PI_HOST"
if [[ "$LAN_IP" == "PI-SDR" || "$LAN_IP" == "pi-sdr" ]]; then
  LAN_IP=$("${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "hostname -I | awk '{print \\$1}'" 2>/dev/null || echo "$PI_HOST")
fi
pass "dashboard URL: http://${LAN_IP}:8070"
pass "named configs endpoint: http://${LAN_IP}:8070/api/config/named"
finish
