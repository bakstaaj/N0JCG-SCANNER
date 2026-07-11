#!/usr/bin/env bash
set -Eeuo pipefail
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
LOG_FILE="$LOG_DIR/deploy_v0_4d3f_radioreference_picker_${STAMP}.txt"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
mkdir -p "$LOG_DIR" 2>/dev/null || true
exec > >(tee "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
finish(){
  local rc="$1"
  echo "UPLOAD_FILE_MSYS=$LOG_FILE"
  echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"
  echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [[ "$rc" == "0" && "$FAIL_COUNT" == "0" ]]; then
    echo "FINAL: PASS"
  else
    echo "FINAL: FAIL"
  fi
}
trap 'rc=$?; fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish "$rc"; exit "$rc"' ERR

cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
pass "using forced Pi host $PI_HOST"

python3 -m py_compile src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py
pass "local Python syntax validation passed"
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
  pass "local node syntax validation passed"
else
  warn "node not found locally; skipped web/app.js syntax check"
fi

SSH=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
SCP=(scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if command -v sshpass >/dev/null 2>&1; then
  if [[ -n "${PI_PASSWORD:-}" ]]; then
    export SSHPASS="$PI_PASSWORD"
  elif [[ -n "${SSHPASS:-}" ]]; then
    export SSHPASS="$SSHPASS"
  else
    read -r -s -p "Pi password for ${PI_USER}@${PI_HOST}: " PI_PASSWORD_INPUT
    echo
    export SSHPASS="$PI_PASSWORD_INPUT"
  fi
  SSH=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  SCP=(sshpass -e scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  pass "sshpass enabled for this deploy"
else
  warn "sshpass not found; ssh/scp may prompt interactively"
fi

tmp="/tmp/pi_p25_v0_4d3f_${STAMP}.tar.gz"
tar -czf "$tmp" src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py web/app.js
pass "created local payload $tmp"

"${SSH[@]}" "$PI_USER@$PI_HOST" "test -d '$PI_REPO'"
pass "remote repo exists: $PI_REPO"
"${SCP[@]}" "$tmp" "$PI_USER@$PI_HOST:/tmp/$(basename "$tmp")"
pass "uploaded patch payload"

"${SSH[@]}" "$PI_USER@$PI_HOST" "set -euo pipefail
cd '$PI_REPO'
mkdir -p runtime/patch_backups/deploy_v0_4d3f_${STAMP}
cp -p src/pi_p25_scanner/radioreference_import.py runtime/patch_backups/deploy_v0_4d3f_${STAMP}/radioreference_import.py || true
cp -p src/pi_p25_scanner/backend.py runtime/patch_backups/deploy_v0_4d3f_${STAMP}/backend.py || true
cp -p web/app.js runtime/patch_backups/deploy_v0_4d3f_${STAMP}/app.js || true
tar -xzf /tmp/$(basename "$tmp")
python3 -m py_compile src/pi_p25_scanner/radioreference_import.py src/pi_p25_scanner/backend.py
if command -v node >/dev/null 2>&1; then node --check web/app.js; fi
sudo systemctl restart pi-p25-scanner.service
sleep 2
systemctl is-active --quiet pi-p25-scanner.service
python3 - <<'PY'
import json
import urllib.error
import urllib.request

def show(title, code, body):
    print(f'=== {title} HTTP {code} ===')
    print(body[:12000])
    print(f'=== END {title} ===')

def get(path):
    url = 'http://127.0.0.1:8070' + path
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')

def post(path, payload):
    url = 'http://127.0.0.1:8070' + path
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type':'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')

code, body = get('/api/status')
show('/api/status', code, body)
if code != 200:
    raise SystemExit(10)

code, body = get('/api/radioreference/status')
show('/api/radioreference/status', code, body)
if code in (404, 500):
    raise SystemExit(11)

code, body = post('/api/radioreference/systems', {'state':'AZ','county':'Maricopa','city':'Mesa'})
show('/api/radioreference/systems', code, body)
if code == 404 or code >= 500:
    raise SystemExit(12)
if code == 400:
    print('WARN: RR systems endpoint returned HTTP 400. Deploy is complete; use the printed body above for the next parser/auth fix.')
else:
    try:
        payload = json.loads(body)
        print(json.dumps({k: payload.get(k) for k in ('ok','state_id','county_id','source_count','system_count')}, indent=2, sort_keys=True))
    except Exception as exc:
        print(f'WARN: could not summarize RR systems body: {exc}')
PY
"
pass "remote deploy completed; RR picker probe body captured"
rm -f "$tmp"
finish 0
