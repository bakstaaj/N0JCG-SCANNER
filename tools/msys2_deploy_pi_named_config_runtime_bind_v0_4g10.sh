#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="deploy_v0_4g10_named_config_runtime_bind"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/${SCRIPT_NAME}_${STAMP}.txt"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
exec > >(tee "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
finish(){
  local rc="$1"
  echo "UPLOAD_FILE_MSYS=$LOG_FILE"
  echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"
  echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [[ "$rc" -eq 0 && "$FAIL_COUNT" -eq 0 ]]; then echo "FINAL: PASS"; else echo "FINAL: FAIL"; fi
  exit "$rc"
}
trap 'rc=$?; fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish "$rc"' ERR

find_repo_root(){
  local d="$(pwd)"
  while [[ "$d" != "/" ]]; do
    if [[ -d "$d/.git" && -f "$d/src/pi_p25_scanner/backend.py" ]]; then printf '%s\n' "$d"; return 0; fi
    d="$(dirname "$d")"
  done
  for d in "$HOME/sdrdev/PI-P25-SCANNER" "/home/jim/sdrdev/PI-P25-SCANNER"; do
    if [[ -d "$d/.git" && -f "$d/src/pi_p25_scanner/backend.py" ]]; then printf '%s\n' "$d"; return 0; fi
  done
  return 1
}
REPO_ROOT="$(find_repo_root)"
cd "$REPO_ROOT"
pass "repo root detected: $REPO_ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-PI-SDR}"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
SSHPASS_VALUE="${PI_PASSWORD:-${SSHPASS:-}}"
if [[ -z "$SSHPASS_VALUE" ]]; then
  fail "PI_PASSWORD or SSHPASS must be set in .env or environment"
  finish 1
fi
command -v sshpass >/dev/null || { fail "sshpass not found in MSYS2"; finish 1; }
command -v scp >/dev/null || { fail "scp not found in MSYS2"; finish 1; }
command -v ssh >/dev/null || { fail "ssh not found in MSYS2"; finish 1; }
pass "deploy prerequisites present"

for f in src/pi_p25_scanner/named_config_runtime.py src/pi_p25_scanner/config_store.py src/pi_p25_scanner/backend.py; do
  [[ -f "$f" ]] || { fail "missing deploy file: $f"; finish 1; }
done
pass "deploy files present"

TARBALL="/tmp/pi_p25_v0_4g10_named_config_runtime_${STAMP}.tar.gz"
tar -czf "$TARBALL" src/pi_p25_scanner/named_config_runtime.py src/pi_p25_scanner/config_store.py src/pi_p25_scanner/backend.py
pass "created deploy tarball: $TARBALL"

SSH_BASE=(sshpass -p "$SSHPASS_VALUE" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
SCP_BASE=(sshpass -p "$SSHPASS_VALUE" scp -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
"${SCP_BASE[@]}" "$TARBALL" "${PI_USER}@${PI_HOST}:/tmp/$(basename "$TARBALL")"
pass "copied deploy tarball to ${PI_HOST}"

REMOTE_TARBALL="/tmp/$(basename "$TARBALL")"
"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" bash -s -- "$PI_REPO" "$REMOTE_TARBALL" <<'REMOTE'
set -Eeuo pipefail
PI_REPO="$1"
REMOTE_TARBALL="$2"
cd "$PI_REPO"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p runtime/settings/backups
for f in src/pi_p25_scanner/named_config_runtime.py src/pi_p25_scanner/config_store.py src/pi_p25_scanner/backend.py; do
  [[ -f "$f" ]] && cp -p "$f" "runtime/settings/backups/$(basename "$f").v0_4g10.${stamp}.bak"
done
tar -xzf "$REMOTE_TARBALL"
python3 -m py_compile src/pi_p25_scanner/named_config_runtime.py src/pi_p25_scanner/config_store.py src/pi_p25_scanner/backend.py
python3 - <<'PY'
from pathlib import Path
backend = Path('src/pi_p25_scanner/backend.py').read_text(encoding='utf-8')
config_store = Path('src/pi_p25_scanner/config_store.py').read_text(encoding='utf-8')
runtime = Path('src/pi_p25_scanner/named_config_runtime.py').read_text(encoding='utf-8')
required = [
    ('backend binding', 'ScannerManager.named_configs_payload' in backend),
    ('GET endpoint', '"/api/config/named"' in backend),
    ('POST save endpoint', '"/api/config/named/save"' in backend),
    ('config_store wrapper', 'def list_named_configs(include_invalid: bool = False)' in config_store),
    ('runtime helper', 'def list_named_configs(include_invalid: bool = False)' in runtime),
]
missing = [name for name, ok in required if not ok]
if missing:
    raise SystemExit('missing deployed markers: ' + ', '.join(missing))
print('REMOTE_MARKERS_OK')
PY
sudo systemctl restart pi-p25-scanner.service
REMOTE
pass "remote files installed, compiled, and service restart requested"

probe_remote(){
  local path="$1"
  "${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" python3 - "$path" <<'PY'
import json
import sys
import time
import urllib.request
path = sys.argv[1]
url = 'http://127.0.0.1:8070' + path
last = None
for _ in range(45):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            body = r.read(200000)
        text = body.decode('utf-8', errors='replace')
        if path.endswith('/named'):
            payload = json.loads(text)
            if payload.get('ok') is not True or 'configs' not in payload:
                raise RuntimeError('named payload missing ok/configs: ' + text[:300])
        print('PROBE_OK', url)
        sys.exit(0)
    except Exception as exc:
        last = exc
        time.sleep(1)
print('PROBE_FAIL', url, repr(last))
sys.exit(1)
PY
}
probe_remote "/api/status"
pass "remote /api/status probe passed"
probe_remote "/api/config/named"
pass "remote /api/config/named probe passed"

"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" bash -s <<'REMOTE'
set -euo pipefail
printf 'SERVICE_STATUS_BEGIN\n'
systemctl --no-pager --lines=8 status pi-p25-scanner.service || true
printf 'LAN_IP_BEGIN\n'
hostname -I | awk '{print $1}'
REMOTE
pass "printed remote service status and LAN IP"

finish 0
