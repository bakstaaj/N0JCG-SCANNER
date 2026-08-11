#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="deploy_v0_4h_talkgroup_activity_display"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/${VERSION}_${STAMP}.txt"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
exec > >(tee -a "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); echo "UPLOAD_FILE_MSYS=$LOG_FILE"; echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"; echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; echo "FINAL: FAIL"; exit 1; }
trap 'fail "deploy aborted unexpectedly at line $LINENO rc=$?"' ERR

if [[ ! -f src/pi_p25_scanner/backend.py || ! -f src/pi_p25_scanner/talkgroup_activity.py ]]; then
  fail "run this from the scanner repo root after applying V0.4H"
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
SSHPASS_VALUE="${PI_PASSWORD:-${SSHPASS:-}}"
if [[ -z "$SSHPASS_VALUE" ]]; then
  read -r -s -p "Pi password for ${PI_USER}@${PI_HOST}: " SSHPASS_VALUE
  echo
fi
export SSHPASS="$SSHPASS_VALUE"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
REMOTE="${PI_USER}@${PI_HOST}"
pass "using Pi target ${REMOTE}:${PI_REPO}"

if ! command -v sshpass >/dev/null 2>&1; then
  fail "sshpass is required in MSYS2"
fi

TARBALL="/tmp/pi_p25_v0_4h_talkgroup_activity_${STAMP}.tar.gz"
tar -czf "$TARBALL" src/pi_p25_scanner/backend.py src/pi_p25_scanner/talkgroup_activity.py
pass "created deploy tarball: $TARBALL"

sshpass -e scp -O "${SSH_OPTS[@]}" "$TARBALL" "$REMOTE:/tmp/$(basename "$TARBALL")"
pass "copied deploy tarball to ${PI_HOST}"

REMOTE_SCRIPT=$(cat <<'REMOTE_SH'
set -Eeuo pipefail
cd "$PI_REPO"
mkdir -p .deploy_backups/v0_4h_talkgroup_activity
STAMP_REMOTE="$(date -u +%Y%m%dT%H%M%SZ)"
cp -p src/pi_p25_scanner/backend.py ".deploy_backups/v0_4h_talkgroup_activity/backend.py.${STAMP_REMOTE}" 2>/dev/null || true
cp -p src/pi_p25_scanner/talkgroup_activity.py ".deploy_backups/v0_4h_talkgroup_activity/talkgroup_activity.py.${STAMP_REMOTE}" 2>/dev/null || true
tar -xzf "/tmp/$TARBALL_NAME" -C "$PI_REPO"
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/talkgroup_activity.py
sudo systemctl restart pi-p25-scanner.service
sleep 3
python3 - <<'PY'
import json, time, urllib.request
last = None
for _ in range(20):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8070/api/status', timeout=2) as response:
            data = json.loads(response.read().decode('utf-8'))
        print('STATUS_OK_BEGIN')
        print(json.dumps({
            'ok': data.get('ok'),
            'scanner_state': data.get('scanner_state'),
            'start_enabled': data.get('decoder_process', {}).get('start_enabled'),
            'active_tgid': data.get('active_tgid'),
            'active_talkgroup_label': data.get('active_talkgroup_label'),
            'last_active_tgid': data.get('last_active_tgid'),
            'last_active_talkgroup_label': data.get('last_active_talkgroup_label'),
            'runtime_status_keys': sorted(list((data.get('runtime_status') or {}).keys())),
        }, indent=2, sort_keys=True))
        print('STATUS_OK_END')
        break
    except Exception as exc:
        last = exc
        time.sleep(1)
else:
    raise SystemExit(f'/api/status did not respond: {last!r}')
PY
REMOTE_SH
)
sshpass -e ssh "${SSH_OPTS[@]}" "$REMOTE" "PI_REPO='$PI_REPO' TARBALL_NAME='$(basename "$TARBALL")' bash -s" <<< "$REMOTE_SCRIPT" || {
  echo "SERVICE_STATUS_BEGIN"
  sshpass -e ssh "${SSH_OPTS[@]}" "$REMOTE" "systemctl status pi-p25-scanner.service --no-pager || true"
  echo "JOURNAL_BEGIN"
  sshpass -e ssh "${SSH_OPTS[@]}" "$REMOTE" "journalctl -u pi-p25-scanner.service -n 120 --no-pager || true"
  fail "remote deploy/probe failed"
}
pass "remote files installed, compiled, service restarted, and /api/status responded"

cat > tools/msys2_probe_pi_talkgroup_activity_v0_4h.sh <<'PROBE'
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ -f .env ]]; then set -a; source .env; set +a; fi
PI_HOST="${PI_HOST:-192.168.254.63}"
for i in $(seq 1 30); do
  python3 - "$PI_HOST" <<'PY'
import json, sys, urllib.request
host = sys.argv[1]
with urllib.request.urlopen(f'http://{host}:8070/api/status', timeout=3) as response:
    data = json.loads(response.read().decode('utf-8'))
print(json.dumps({
    'scanner_state': data.get('scanner_state'),
    'active_tgid': data.get('active_tgid'),
    'active_talkgroup_label': data.get('active_talkgroup_label'),
    'last_active_tgid': data.get('last_active_tgid'),
    'last_active_talkgroup_label': data.get('last_active_talkgroup_label'),
    'last_active_updated_utc': data.get('last_active_updated_utc'),
    'talkgroup_parser': (data.get('runtime_status') or {}).get('talkgroup_activity_parser'),
}, indent=2, sort_keys=True))
PY
  sleep 3
done
PROBE
chmod +x tools/msys2_probe_pi_talkgroup_activity_v0_4h.sh
pass "wrote optional talkgroup activity probe helper"

echo "UPLOAD_FILE_MSYS=$LOG_FILE"
echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"
echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
echo "FINAL: PASS"
