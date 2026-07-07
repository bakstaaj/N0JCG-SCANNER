#!/usr/bin/env bash
set -Eeuo pipefail

PATCH_NAME="deploy_v0_4g7_named_config_manager_fix"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/${PATCH_NAME}_${STAMP}.txt"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
exec > >(tee "$LOG_FILE") 2>&1

pass() { echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
finish() {
  local rc=$?
  echo "UPLOAD_FILE_MSYS=$LOG_FILE"
  local win_path="$LOG_FILE"
  if command -v cygpath >/dev/null 2>&1; then
    win_path="$(cygpath -w "$LOG_FILE" 2>/dev/null || printf '%s' "$LOG_FILE")"
  fi
  echo "UPLOAD_FILE_WINDOWS=$win_path"
  echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [[ $rc -eq 0 && $FAIL_COUNT -eq 0 ]]; then
    echo "FINAL: PASS"
  else
    echo "FINAL: FAIL"
  fi
  exit $rc
}
trap finish EXIT
trap 'fail "deploy aborted unexpectedly at line $LINENO rc=$?"; exit 1' ERR

if [[ ! -d .git || ! -f src/pi_p25_scanner/backend.py ]]; then
  fail "run this from the PI-P25-SCANNER repository root"
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
PI_HOST="${PI_HOST:-PI-SDR}"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
PI_PASSWORD="${PI_PASSWORD:-${SSHPASS:-}}"
if [[ -z "$PI_PASSWORD" ]]; then
  fail "PI_PASSWORD or SSHPASS must be set in .env or environment"
  exit 1
fi
export SSHPASS="$PI_PASSWORD"

command -v sshpass >/dev/null 2>&1 || { fail "sshpass is required in MSYS2"; exit 1; }
command -v tar >/dev/null 2>&1 || { fail "tar is required"; exit 1; }
pass "deploy prerequisites present"

TAR_PATH="/tmp/pi_p25_v0_4g7_named_config_manager_${STAMP}.tar.gz"
tar -czf "$TAR_PATH" src/pi_p25_scanner/backend.py
pass "created deploy tarball: $TAR_PATH"

SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
sshpass -e scp -O "${SSH_OPTS[@]}" "$TAR_PATH" "${PI_USER}@${PI_HOST}:/tmp/$(basename "$TAR_PATH")"
pass "copied deploy tarball to ${PI_HOST}"

sshpass -e ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}" "PI_REPO='$PI_REPO' TAR_NAME='$(basename "$TAR_PATH")' bash -s" <<'REMOTE'
set -Eeuo pipefail
cd "$PI_REPO"
mkdir -p runtime/settings/backups
if [[ -f src/pi_p25_scanner/backend.py ]]; then
  cp -p src/pi_p25_scanner/backend.py "runtime/settings/backups/backend_v0_4g7_$(date -u +%Y%m%dT%H%M%SZ).py"
fi
tar -xzf "/tmp/$TAR_NAME" -C "$PI_REPO"
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/config_store.py
PYTHONPATH="$PI_REPO/src" python3 - <<'PY'
from pi_p25_scanner.config_store import list_named_configs, save_named_config, load_named_config, delete_named_config
from pi_p25_scanner.backend import MANAGER
assert hasattr(MANAGER, 'named_configs_payload')
assert callable(MANAGER.named_configs_payload)
payload = MANAGER.named_configs_payload()
assert isinstance(payload, dict)
print('REMOTE_IMPORT_MANAGER_OK')
PY
if systemctl list-unit-files pi-p25-scanner.service >/dev/null 2>&1; then
  sudo systemctl restart pi-p25-scanner.service
else
  echo "WARN: pi-p25-scanner.service not found"
fi
REMOTE
pass "remote backend deployed and service restart requested"

sshpass -e ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}" "python3 - <<'PY'
import json, time, urllib.request, urllib.error
urls = [
    'http://127.0.0.1:8070/api/status',
    'http://127.0.0.1:8070/api/config/named',
]
deadline = time.time() + 45
last = ''
while time.time() < deadline:
    try:
        results = []
        for url in urls:
            with urllib.request.urlopen(url, timeout=3) as response:
                body = response.read().decode('utf-8', 'replace')
                payload = json.loads(body)
                results.append((url, response.status, payload.get('ok')))
        for url, status, ok in results:
            print(f'PROBE_OK {status} {url} ok={ok}')
        raise SystemExit(0)
    except Exception as exc:
        last = repr(exc)
        time.sleep(2)
print('PROBE_FAIL', last)
raise SystemExit(1)
PY"
pass "Pi backend /api/status and /api/config/named probes passed"

sshpass -e ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}" "hostname -I | awk '{print \$1}'" | while read -r ip; do
  [[ -n "$ip" ]] && echo "PI_LAN_URL=http://$ip:8070"
done
pass "deployment complete"
