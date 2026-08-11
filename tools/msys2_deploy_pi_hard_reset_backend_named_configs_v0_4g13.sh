#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="deploy_v0_4g13_hard_reset_backend_named_configs"
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

if [[ ! -d .git || ! -f src/pi_p25_scanner/backend.py || ! -f src/pi_p25_scanner/config_store.py ]]; then
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

python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/config_store.py
pass "local python compile passed"

tarball="/tmp/pi_p25_v0_4g13_hard_reset_backend_${STAMP}.tar.gz"
tar -czf "$tarball" src/pi_p25_scanner/backend.py src/pi_p25_scanner/config_store.py
pass "created deploy tarball: $tarball"

"${SCP_BASE[@]}" "$tarball" "${PI_USER}@${PI_HOST}:/tmp/$(basename "$tarball")"
pass "copied deploy tarball to ${PI_HOST}"

remote_script=$(cat <<'REMOTE'
set -Eeuo pipefail
repo="$1"
tarball="$2"
cd "$repo"
mkdir -p runtime/patch-backups
backup_dir="runtime/patch-backups/v0_4g13_hard_reset_backend_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
for f in src/pi_p25_scanner/backend.py src/pi_p25_scanner/config_store.py; do
  if [[ -f "$f" ]]; then cp -p "$f" "$backup_dir/$(basename "$f").bak"; fi
done
tar -xzf "$tarball" -C "$repo"
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/config_store.py
sudo systemctl restart pi-p25-scanner.service
sleep 1
sudo systemctl --no-pager status pi-p25-scanner.service | sed -n '1,20p' || true
REMOTE
)
"${SSH_BASE[@]}" bash -s -- "$PI_REPO" "/tmp/$(basename "$tarball")" <<< "$remote_script"
pass "remote files installed, compiled, and service restart requested"

probe_remote=$(cat <<'REMOTE'
set -Eeuo pipefail
python3 - <<'PY'
import json, time, urllib.request, urllib.error
urls = [
    'http://127.0.0.1:8070/api/status',
    'http://127.0.0.1:8070/api/config/named',
]
last = None
for _ in range(30):
    try:
        for url in urls:
            with urllib.request.urlopen(url, timeout=2) as r:
                body = r.read(200000)
                payload = json.loads(body.decode('utf-8'))
                if not isinstance(payload, dict):
                    raise RuntimeError(f'{url} did not return JSON object')
                if payload.get('ok') is False and url.endswith('/api/status'):
                    raise RuntimeError(f'{url} returned ok=false: {payload}')
        print('PROBE_PASS api/status and api/config/named returned JSON')
        raise SystemExit(0)
    except Exception as exc:
        last = exc
        time.sleep(1)
print(f'PROBE_FAIL {last!r}')
raise SystemExit(1)
PY
REMOTE
)
if "${SSH_BASE[@]}" bash -s <<< "$probe_remote"; then
  pass "remote API probes passed"
else
  fail "remote API probe failed; collecting diagnostics"
  diag=$(cat <<'REMOTE'
set +e
echo SERVICE_STATUS_BEGIN
sudo systemctl --no-pager status pi-p25-scanner.service | sed -n '1,80p'
echo JOURNAL_BEGIN
sudo journalctl -u pi-p25-scanner.service -n 160 --no-pager
echo PORTS_BEGIN
(ss -ltnp || netstat -ltnp) 2>/dev/null | grep -E '(:8070|:8072|:18091)' || true
echo BACKEND_HEAD_BEGIN
nl -ba /home/pi/n0jcg-scanner/src/pi_p25_scanner/backend.py | sed -n '1,120p'
echo BACKEND_STATUS_AREA_BEGIN
nl -ba /home/pi/n0jcg-scanner/src/pi_p25_scanner/backend.py | grep -n 'status_payload\|api/status\|MANAGER = ScannerManager' | head -20
REMOTE
)
  "${SSH_BASE[@]}" bash -s <<< "$diag" || true
  exit 1
fi

external_url="http://${PI_HOST}:8070"
echo "UI_URL=$external_url"
echo "NAMED_CONFIG_API=$external_url/api/config/named"
pass "deploy complete"
