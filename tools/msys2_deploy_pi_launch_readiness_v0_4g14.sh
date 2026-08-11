#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="deploy_v0_4g14_launch_readiness"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/${SCRIPT_NAME}_${STAMP}.txt"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
exec > >(tee -a "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
finish(){
  local rc="$1"
  echo "UPLOAD_FILE_MSYS=$LOG_FILE"
  echo "UPLOAD_FILE_WINDOWS=C:\\Users\\jim\\Downloads\\pi-p25-command-logs\\$(basename "$LOG_FILE")"
  echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [[ "$rc" == "0" && "$FAIL_COUNT" == "0" ]]; then echo "FINAL: PASS"; else echo "FINAL: FAIL"; fi
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; fi; finish $rc' EXIT

if [[ ! -d .git || ! -f src/pi_p25_scanner/backend.py ]]; then
  fail "run this deploy helper from the scanner repo root"
  exit 1
fi
pass "repo root detected"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-192.168.254.63}"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
PI_PASSWORD="${PI_PASSWORD:-${SSHPASS:-}}"
if [[ -z "$PI_PASSWORD" ]]; then
  read -r -s -p "Password for ${PI_USER}@${PI_HOST}: " PI_PASSWORD
  echo
fi
export SSHPASS="$PI_PASSWORD"
SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "${PI_USER}@${PI_HOST}")
SCP_BASE=(sshpass -e scp -O -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
pass "using Pi target ${PI_USER}@${PI_HOST}:${PI_REPO}"

python3 -m py_compile src/pi_p25_scanner/backend.py
pass "local backend.py compile passed"

tarball="/tmp/pi_p25_v0_4g14_launch_readiness_${STAMP}.tar.gz"
tar -czf "$tarball" src/pi_p25_scanner/backend.py
pass "created deploy tarball: $tarball"

"${SCP_BASE[@]}" "$tarball" "${PI_USER}@${PI_HOST}:/tmp/$(basename "$tarball")"
pass "copied deploy tarball to ${PI_HOST}"

remote_script=$(cat <<'REMOTE'
set -Eeuo pipefail
repo="$1"
tarball="$2"
cd "$repo"
backup_dir="runtime/patch-backups/v0_4g14_launch_readiness_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
cp -p src/pi_p25_scanner/backend.py "$backup_dir/backend.py.bak" 2>/dev/null || true
tar -xzf "$tarball" -C "$repo"
python3 -m py_compile src/pi_p25_scanner/backend.py
sudo systemctl restart pi-p25-scanner.service
sleep 1
sudo systemctl --no-pager status pi-p25-scanner.service | sed -n '1,25p' || true
REMOTE
)
"${SSH_BASE[@]}" bash -s -- "$PI_REPO" "/tmp/$(basename "$tarball")" <<< "$remote_script"
pass "remote backend.py installed, compiled, and service restart requested"

probe_remote=$(cat <<'REMOTE'
set -Eeuo pipefail
python3 - <<'PY'
import json, time, urllib.request
last = None
payload = None
for _ in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8070/api/status', timeout=2) as r:
            payload = json.loads(r.read(500000).decode('utf-8'))
        if not isinstance(payload, dict):
            raise RuntimeError('status did not return a JSON object')
        print('STATUS_OK scanner_state=' + str(payload.get('scanner_state')))
        dp = payload.get('decoder_process') or {}
        print('START_ENABLED=' + str(dp.get('start_enabled')))
        print('COMMAND_SOURCE=' + str(dp.get('command_source')))
        marker = dp.get('validated_marker') or {}
        print('MARKER_EXISTS=' + str(marker.get('exists')))
        print('MARKER_VALIDATED=' + str(marker.get('validated')))
        if dp.get('start_enabled') is not True:
            print('START_NOT_READY_DETAIL=' + json.dumps(dp, sort_keys=True)[:4000])
            raise SystemExit(2)
        raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as exc:
        last = exc
        time.sleep(1)
print('PROBE_FAIL ' + repr(last))
raise SystemExit(1)
PY
REMOTE
)
if "${SSH_BASE[@]}" bash -s <<< "$probe_remote"; then
  pass "remote /api/status reports Start launch-ready"
else
  rc=$?
  if [[ "$rc" == "2" ]]; then
    warn "remote /api/status responds but Start is still not launch-ready; collecting readiness diagnostics"
  else
    fail "remote /api/status probe failed; collecting diagnostics"
  fi
  diag=$(cat <<'REMOTE'
set +e
echo SERVICE_STATUS_BEGIN
sudo systemctl --no-pager status pi-p25-scanner.service | sed -n '1,80p'
echo JOURNAL_BEGIN
sudo journalctl -u pi-p25-scanner.service -n 120 --no-pager
echo MARKER_BEGIN
ls -l /home/pi/n0jcg-scanner/runtime/settings/op25_validated_rx_command.env 2>&1 || true
sed -n '1,80p' /home/pi/n0jcg-scanner/runtime/settings/op25_validated_rx_command.env 2>/dev/null || true
echo STATUS_BEGIN
python3 - <<'PY'
import urllib.request
try:
    print(urllib.request.urlopen('http://127.0.0.1:8070/api/status', timeout=3).read().decode('utf-8')[:8000])
except Exception as exc:
    print('STATUS_ERROR', repr(exc))
PY
REMOTE
)
  "${SSH_BASE[@]}" bash -s <<< "$diag" || true
  if [[ "$rc" == "2" ]]; then
    exit 0
  fi
  exit 1
fi

echo "UI_URL=http://${PI_HOST}:8070"
pass "deploy complete"
