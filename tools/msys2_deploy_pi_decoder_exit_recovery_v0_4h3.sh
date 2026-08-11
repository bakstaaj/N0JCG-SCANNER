#!/usr/bin/env bash
set -Eeuo pipefail
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/deploy_v0_4h3_decoder_exit_recovery_${TS}.txt"
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
exec > >(tee -a "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); echo "UPLOAD_FILE_MSYS=$LOG_FILE"; echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"; echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; echo "FINAL: FAIL"; exit 1; }
finish(){ echo "UPLOAD_FILE_MSYS=$LOG_FILE"; echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"; echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; echo "FINAL: PASS"; }
trap 'fail "deploy aborted unexpectedly at line $LINENO rc=$?"' ERR

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-192.168.254.63}"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env || true
  set +a
  PI_USER="${PI_USER:-pi}"
  PI_HOST="${PI_HOST:-192.168.254.63}"
  PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
fi
# Force known-good IP unless explicitly overridden by setting PI_HOST before run.
PI_HOST="${PI_HOST:-192.168.254.63}"

ssh_cmd=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "${PI_USER}@${PI_HOST}")
scp_cmd=(scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [[ -n "${SSHPASS:-${PI_PASSWORD:-}}" ]] && command -v sshpass >/dev/null 2>&1; then
  export SSHPASS="${SSHPASS:-$PI_PASSWORD}"
  ssh_cmd=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "${PI_USER}@${PI_HOST}")
  scp_cmd=(sshpass -e scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
fi

[[ -f src/pi_p25_scanner/backend.py ]] || fail "missing backend.py"
pass "deploy prerequisites present"

TAR="/tmp/pi_p25_v0_4h3_decoder_exit_recovery_${TS}.tar.gz"
tar -czf "$TAR" src/pi_p25_scanner/backend.py
pass "created deploy tarball: $TAR"
"${scp_cmd[@]}" "$TAR" "${PI_USER}@${PI_HOST}:/tmp/$(basename "$TAR")"
pass "copied deploy tarball to ${PI_HOST}"

"${ssh_cmd[@]}" bash -s -- "$PI_REPO" "/tmp/$(basename "$TAR")" <<'REMOTE'
set -Eeuo pipefail
repo="$1"; tarball="$2"
cd "$repo"
mkdir -p runtime/patch_backups/deploy_v0_4h3_$(date -u +%Y%m%dT%H%M%SZ)
cp -p src/pi_p25_scanner/backend.py runtime/patch_backups/deploy_v0_4h3_$(date -u +%Y%m%dT%H%M%SZ)/backend.py.bak 2>/dev/null || true
tar -xzf "$tarball" -C "$repo"
python3 -m py_compile src/pi_p25_scanner/backend.py
sudo systemctl restart pi-p25-scanner.service
sleep 2
python3 - <<'PY'
import urllib.request, json, sys, time
for _ in range(20):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8070/api/status', timeout=2) as r:
            data=json.loads(r.read().decode('utf-8'))
        print('STATUS_OK', data.get('scanner_state'), data.get('decoder_process', {}).get('start_enabled'))
        break
    except Exception as exc:
        last=exc
        time.sleep(0.5)
else:
    print('STATUS_PROBE_FAILED', repr(last))
    sys.exit(2)
try:
    with urllib.request.urlopen('http://127.0.0.1:8070/api/activity', timeout=2) as r:
        data=json.loads(r.read().decode('utf-8'))
    print('ACTIVITY_OK', data.get('scanner_state'), data.get('display_tgid'))
except Exception as exc:
    print('ACTIVITY_PROBE_FAILED', repr(exc))
    sys.exit(3)
PY
REMOTE
pass "remote backend deployed, compiled, restarted, and probed"
finish
