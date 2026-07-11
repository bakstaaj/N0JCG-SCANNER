#!/usr/bin/env bash
set -Eeuo pipefail
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
LOG_FILE="$LOG_DIR/deploy_v0_4d3e_radioreference_picker_${STAMP}.txt"
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
mkdir -p "$LOG_DIR" 2>/dev/null || true
exec > >(tee "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
finish(){ local rc="$1"; echo "UPLOAD_FILE_MSYS=$LOG_FILE"; echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"; echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; [[ "$rc" == 0 && "$FAIL_COUNT" == 0 ]] && echo "FINAL: PASS" || echo "FINAL: FAIL"; }
trap 'rc=$?; fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish "$rc"; exit "$rc"' ERR

cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
pass "using forced Pi host $PI_HOST"
python3 -m py_compile src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py
pass "local Python syntax validation passed"
if command -v node >/dev/null 2>&1; then node --check web/app.js; pass "local node syntax validation passed"; else warn "node not found locally"; fi

tmp="/tmp/pi_p25_v0_4d3e_${STAMP}.tar.gz"
tar -czf "$tmp" src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py web/app.js

SSH=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
SCP=(scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [[ -n "${PI_PASSWORD:-${SSHPASS:-}}" ]] && command -v sshpass >/dev/null 2>&1; then
  SSH=(sshpass -p "${PI_PASSWORD:-$SSHPASS}" ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  SCP=(sshpass -p "${PI_PASSWORD:-$SSHPASS}" scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
fi

"${SSH[@]}" "$PI_USER@$PI_HOST" "test -d '$PI_REPO'"
pass "remote repo exists: $PI_REPO"
"${SCP[@]}" "$tmp" "$PI_USER@$PI_HOST:/tmp/$(basename "$tmp")"
pass "uploaded patch payload"
"${SSH[@]}" "$PI_USER@$PI_HOST" "set -euo pipefail
cd '$PI_REPO'
mkdir -p runtime/patch_backups/deploy_v0_4d3e_${STAMP}
cp -p src/pi_p25_scanner/radioreference_import.py runtime/patch_backups/deploy_v0_4d3e_${STAMP}/radioreference_import.py || true
cp -p src/pi_p25_scanner/backend.py runtime/patch_backups/deploy_v0_4d3e_${STAMP}/backend.py || true
cp -p web/app.js runtime/patch_backups/deploy_v0_4d3e_${STAMP}/app.js || true
tar -xzf /tmp/$(basename "$tmp")
python3 -m py_compile src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py
if command -v node >/dev/null 2>&1; then node --check web/app.js; fi
sudo systemctl restart pi-p25-scanner.service
sleep 2
systemctl is-active --quiet pi-p25-scanner.service
curl -fsS http://127.0.0.1:8070/api/status >/tmp/pi_p25_status_v0_4d3e.json
curl -fsS -X POST http://127.0.0.1:8070/api/radioreference/systems -H 'Content-Type: application/json' --data '{"state":"AZ","county":"Maricopa","city":"Mesa"}' >/tmp/pi_p25_rr_systems_v0_4d3e.json
python3 - <<'PY'
import json
from pathlib import Path
p=json.loads(Path('/tmp/pi_p25_rr_systems_v0_4d3e.json').read_text())
print(json.dumps({k:p.get(k) for k in ('ok','state_id','county_id','system_count','source_count')}, indent=2, sort_keys=True))
PY
"
pass "remote deploy and endpoint probe passed"
rm -f "$tmp"
finish 0
