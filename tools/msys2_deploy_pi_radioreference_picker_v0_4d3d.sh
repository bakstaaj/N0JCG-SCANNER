#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_NAME="deploy_v0_4d3d_radioreference_picker_resolution"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/${SCRIPT_NAME}_${STAMP}.txt"
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
exec > >(tee -a "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); echo "UPLOAD_FILE_MSYS=$LOG_FILE"; echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"; echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; echo "FINAL: FAIL"; exit 1; }
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; fi' ERR
finish(){ echo "UPLOAD_FILE_MSYS=$LOG_FILE"; echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"; echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; [[ $FAIL_COUNT -eq 0 ]] && echo "FINAL: PASS" || { echo "FINAL: FAIL"; exit 1; }; }

echo "=== Deploy V0.4D3D RadioReference picker resolution ==="
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [[ -n "${PI_PASSWORD:-${SSHPASS:-}}" ]] && command -v sshpass >/dev/null 2>&1; then
  SSHPASS_VALUE="${PI_PASSWORD:-${SSHPASS:-}}"
  SSH=(sshpass -p "$SSHPASS_VALUE" ssh "${SSH_OPTS[@]}")
  SCP=(sshpass -p "$SSHPASS_VALUE" scp -O "${SSH_OPTS[@]}")
else
  SSH=(ssh "${SSH_OPTS[@]}")
  SCP=(scp -O "${SSH_OPTS[@]}")
fi

[[ -f src/pi_p25_scanner/radioreference_import.py ]] || fail "missing radioreference_import.py"
[[ -f src/pi_p25_scanner/backend.py ]] || fail "missing backend.py"
[[ -f web/app.js ]] || fail "missing web/app.js"
python3 -m py_compile src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py
pass "local python compile passed"
if command -v node >/dev/null 2>&1; then node --check web/app.js && pass "local node check passed"; else warn "node not found locally"; fi

tarball="/tmp/pi_p25_v0_4d3d_rr_picker_${STAMP}.tar.gz"
tar -czf "$tarball" src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py web/app.js
pass "created deploy tarball"

"${SSH[@]}" "$PI_USER@$PI_HOST" "mkdir -p '$PI_REPO/runtime/patch_backups/deploy_v0_4d3d_${STAMP}'"
"${SCP[@]}" "$tarball" "$PI_USER@$PI_HOST:/tmp/pi_p25_v0_4d3d_rr_picker.tar.gz"
pass "uploaded files to $PI_HOST"

"${SSH[@]}" "$PI_USER@$PI_HOST" "set -Eeuo pipefail
cd '$PI_REPO'
cp src/pi_p25_scanner/radioreference_import.py runtime/patch_backups/deploy_v0_4d3d_${STAMP}/radioreference_import.py.bak 2>/dev/null || true
cp src/pi_p25_scanner/backend.py runtime/patch_backups/deploy_v0_4d3d_${STAMP}/backend.py.bak 2>/dev/null || true
cp web/app.js runtime/patch_backups/deploy_v0_4d3d_${STAMP}/app.js.bak 2>/dev/null || true
tar -xzf /tmp/pi_p25_v0_4d3d_rr_picker.tar.gz
python3 -m py_compile src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py
if command -v node >/dev/null 2>&1; then node --check web/app.js; fi
sudo systemctl restart pi-p25-scanner.service
sleep 2
systemctl is-active --quiet pi-p25-scanner.service
python3 - <<'PYREMOTE'
import json, urllib.request
for path in ('/api/status', '/api/radioreference/status'):
    with urllib.request.urlopen('http://127.0.0.1:8070' + path, timeout=5) as response:
        payload = json.loads(response.read().decode('utf-8', 'replace'))
    print(path, json.dumps({k: payload.get(k) for k in ('ok','configured','scanner_state','zeep') if k in payload}, sort_keys=True))
PYREMOTE
"
pass "remote deploy, restart, and basic probes passed"

cat > tools/msys2_probe_pi_radioreference_picker_v0_4d3d.sh <<'PROBE'
#!/usr/bin/env bash
set -Eeuo pipefail
STATE="${1:-AZ}"
COUNTY="${2:-Maricopa}"
CITY="${3:-Mesa}"
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [[ -n "${PI_PASSWORD:-${SSHPASS:-}}" ]] && command -v sshpass >/dev/null 2>&1; then
  SSHPASS_VALUE="${PI_PASSWORD:-${SSHPASS:-}}"
  SSH=(sshpass -p "$SSHPASS_VALUE" ssh "${SSH_OPTS[@]}")
else
  SSH=(ssh "${SSH_OPTS[@]}")
fi
"${SSH[@]}" "$PI_USER@$PI_HOST" STATE="$STATE" COUNTY="$COUNTY" CITY="$CITY" python3 - <<'PYREMOTE'
import json, os, urllib.request
payload = {"state": os.environ.get("STATE", "AZ"), "county": os.environ.get("COUNTY", "Maricopa"), "city": os.environ.get("CITY", "Mesa"), "categories": ["Fire", "EMS", "Law Enforcement", "Interop"]}
body = json.dumps(payload).encode()
req = urllib.request.Request('http://127.0.0.1:8070/api/radioreference/systems', data=body, headers={'Content-Type':'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=45) as response:
    systems = json.loads(response.read().decode('utf-8', 'replace'))
print(json.dumps(systems, indent=2, sort_keys=True))
first = None
for item in systems.get('systems', []):
    if item.get('system_id'):
        first = item['system_id']
        break
if first:
    body = json.dumps({'system_id': first}).encode()
    req = urllib.request.Request('http://127.0.0.1:8070/api/radioreference/sites', data=body, headers={'Content-Type':'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=45) as response:
        sites = json.loads(response.read().decode('utf-8', 'replace'))
    print('--- sites for first system ---')
    print(json.dumps(sites, indent=2, sort_keys=True))
PYREMOTE
PROBE
chmod +x tools/msys2_probe_pi_radioreference_picker_v0_4d3d.sh
pass "created probe helper tools/msys2_probe_pi_radioreference_picker_v0_4d3d.sh"

rm -f "$tarball"
finish
