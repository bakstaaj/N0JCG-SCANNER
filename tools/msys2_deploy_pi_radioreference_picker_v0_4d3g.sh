#!/usr/bin/env bash
set -Eeuo pipefail
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/deploy_v0_4d3g_radioreference_picker_${STAMP}.txt"
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
exec > >(tee "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
finish(){ echo "UPLOAD_FILE_MSYS=$LOG_FILE"; echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"; echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; if [[ "$FAIL_COUNT" -eq 0 ]]; then echo "FINAL: PASS"; else echo "FINAL: FAIL"; fi; }
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; fi; finish' EXIT

echo "=== Deploy V0.4D3G RadioReference explicit picker ==="
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"

[[ -f src/pi_p25_scanner/backend.py ]] || { fail "run from repo root"; exit 1; }
for f in src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_import.py web/app.js; do [[ -f "$f" ]] || { fail "missing $f"; exit 1; }; done
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_import.py
pass "local python compile passed"
if command -v node >/dev/null 2>&1; then node --check web/app.js; pass "local node check passed"; else warn "node not found locally; skipped"; fi

SSHPASS_CMD=()
if command -v sshpass >/dev/null 2>&1; then
  if [[ -z "${PI_PASSWORD:-}${SSHPASS:-}" ]]; then
    read -r -s -p "Pi password for ${PI_USER}@${PI_HOST}: " PI_PASSWORD
    echo
  fi
  export SSHPASS="${PI_PASSWORD:-${SSHPASS:-}}"
  SSHPASS_CMD=(sshpass -e)
else
  warn "sshpass not found; ssh/scp may prompt for password more than once"
fi
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
TAR_FILE="/tmp/pi_p25_v0_4d3g_${STAMP}.tar.gz"
tar -czf "$TAR_FILE" src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_import.py web/app.js
"${SSHPASS_CMD[@]}" scp -O "${SSH_OPTS[@]}" "$TAR_FILE" "${PI_USER}@${PI_HOST}:/tmp/pi_p25_v0_4d3g.tar.gz"
pass "uploaded patch bundle to ${PI_HOST}"
"${SSHPASS_CMD[@]}" ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}" "set -Eeuo pipefail
cd '$PI_REPO'
mkdir -p runtime/patch_backups/deploy_v0_4d3g_${STAMP}
cp src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_import.py web/app.js runtime/patch_backups/deploy_v0_4d3g_${STAMP}/ 2>/dev/null || true
tar -xzf /tmp/pi_p25_v0_4d3g.tar.gz
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_import.py
if command -v node >/dev/null 2>&1; then node --check web/app.js; fi
sudo systemctl restart pi-p25-scanner.service
sleep 2
curl -fsS --max-time 5 http://127.0.0.1:8070/api/status >/tmp/pi_p25_status_v0_4d3g.json
python3 - <<'PYREMOTE'
import json
p=json.load(open('/tmp/pi_p25_status_v0_4d3g.json'))
print(json.dumps({'ok':p.get('ok'), 'state':p.get('scanner_state'), 'event':p.get('last_event')}, indent=2))
PYREMOTE
"
pass "remote deploy, compile, restart, and status probe passed"

BODY_FILE="/tmp/pi_p25_rr_systems_v0_4d3g_${STAMP}.json"
HTTP_CODE=$("${SSHPASS_CMD[@]}" ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}" "curl -sS --max-time 25 -o /tmp/pi_p25_rr_systems_v0_4d3g.json -w '%{http_code}' -X POST http://127.0.0.1:8070/api/radioreference/systems -H 'Content-Type: application/json' --data '{\"state\":\"AZ\",\"county\":\"Maricopa\",\"city\":\"Mesa\"}' || true")
echo "RR systems HTTP ${HTTP_CODE}"
"${SSHPASS_CMD[@]}" ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}" "cat /tmp/pi_p25_rr_systems_v0_4d3g.json 2>/dev/null || true"
if [[ "$HTTP_CODE" == "500" || "$HTTP_CODE" == "000" ]]; then fail "RR systems endpoint returned HTTP ${HTTP_CODE}"; exit 1; fi
pass "RR systems endpoint responded HTTP ${HTTP_CODE}"
