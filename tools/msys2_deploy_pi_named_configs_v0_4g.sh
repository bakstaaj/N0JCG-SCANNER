#!/usr/bin/env bash
set -Eeuo pipefail

DEPLOY_NAME="deploy_named_configs_v0_4g"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/${DEPLOY_NAME}_${STAMP}.txt"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
exec > >(tee "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
finish(){
  local rc=$?
  echo "UPLOAD_FILE_MSYS=$LOG_FILE"
  local win_path="$LOG_FILE"
  if command -v cygpath >/dev/null 2>&1; then win_path="$(cygpath -w "$LOG_FILE" 2>/dev/null || printf '%s' "$LOG_FILE")"; fi
  echo "UPLOAD_FILE_WINDOWS=$win_path"
  echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [[ $rc -eq 0 && $FAIL_COUNT -eq 0 ]]; then echo "FINAL: PASS"; else echo "FINAL: FAIL"; fi
  exit $rc
}
trap finish EXIT
trap 'fail "deploy aborted unexpectedly at line $LINENO rc=$?"; exit 1' ERR

if [[ ! -d .git || ! -f src/pi_p25_scanner/backend.py || ! -f web/app.js ]]; then
  fail "run from scanner repo root"
  exit 1
fi
pass "repository root looks correct"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  pass "loaded .env"
else
  warn ".env not found; using environment/default Pi connection values"
fi

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-PI-SDR}"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
SSHPASS_VALUE="${PI_PASSWORD:-${SSHPASS:-}}"
if [[ -z "$SSHPASS_VALUE" ]]; then
  fail "PI_PASSWORD or SSHPASS must be set in .env or environment"
  exit 1
fi
if ! command -v sshpass >/dev/null 2>&1; then
  fail "sshpass is required in MSYS2"
  exit 1
fi
pass "Pi target ${PI_USER}@${PI_HOST}:${PI_REPO}"

python3 -m py_compile src/pi_p25_scanner/config_store.py src/pi_p25_scanner/backend.py
pass "local backend python compile passed"
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
  pass "local app.js node syntax passed"
else
  warn "node unavailable locally; skipping node --check"
fi

tmp="runtime/deploy_${DEPLOY_NAME}_${STAMP}"
rm -rf "$tmp"
mkdir -p "$tmp/src/pi_p25_scanner" "$tmp/web"
cp -p src/pi_p25_scanner/config_store.py "$tmp/src/pi_p25_scanner/config_store.py"
cp -p src/pi_p25_scanner/backend.py "$tmp/src/pi_p25_scanner/backend.py"
cp -p web/index.html "$tmp/web/index.html"
cp -p web/app.js "$tmp/web/app.js"
cp -p web/app.css "$tmp/web/app.css"
cat > "$tmp/remote_apply.sh" <<'REMOTE'
#!/usr/bin/env bash
set -Eeuo pipefail
cd "$1"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="runtime/settings/backups/v0_4g_named_configs_${stamp}"
mkdir -p "$backup/src/pi_p25_scanner" "$backup/web" runtime/settings/configs
for f in src/pi_p25_scanner/config_store.py src/pi_p25_scanner/backend.py web/index.html web/app.js web/app.css; do
  if [[ -f "$f" ]]; then
    mkdir -p "$backup/$(dirname "$f")"
    cp -p "$f" "$backup/$f"
  fi
done
cp -p /tmp/pi_p25_v0_4g/src/pi_p25_scanner/config_store.py src/pi_p25_scanner/config_store.py
cp -p /tmp/pi_p25_v0_4g/src/pi_p25_scanner/backend.py src/pi_p25_scanner/backend.py
cp -p /tmp/pi_p25_v0_4g/web/index.html web/index.html
cp -p /tmp/pi_p25_v0_4g/web/app.js web/app.js
cp -p /tmp/pi_p25_v0_4g/web/app.css web/app.css
python3 -m py_compile src/pi_p25_scanner/config_store.py src/pi_p25_scanner/backend.py
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
else
  grep -q 'refreshNamedConfigsBtn' web/app.js
fi
systemctl --user restart pi-p25-scanner.service 2>/dev/null || sudo systemctl restart pi-p25-scanner.service
python3 - <<'PY'
import json, time, urllib.request
last = None
for _ in range(45):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8070/api/config/named', timeout=1.0) as r:
            data = json.loads(r.read().decode('utf-8'))
        if data.get('ok') is True and isinstance(data.get('configs'), list):
            print('PROBE_PASS /api/config/named configs=%d' % len(data.get('configs')))
            break
    except Exception as exc:
        last = exc
        time.sleep(1)
else:
    raise SystemExit('PROBE_FAIL /api/config/named %r' % (last,))
PY
echo "REMOTE_FINAL: PASS"
REMOTE
chmod +x "$tmp/remote_apply.sh"
pass "created deploy staging files"

rm -f "$tmp.tar.gz"
tar -C "$tmp" -czf "$tmp.tar.gz" .
pass "created deploy tarball"

ssh_common=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
sshpass -p "$SSHPASS_VALUE" ssh "${ssh_common[@]}" "$PI_USER@$PI_HOST" "rm -rf /tmp/pi_p25_v0_4g && mkdir -p /tmp/pi_p25_v0_4g"
sshpass -p "$SSHPASS_VALUE" scp -O "${ssh_common[@]}" "$tmp.tar.gz" "$PI_USER@$PI_HOST:/tmp/pi_p25_v0_4g.tar.gz"
pass "copied deploy tarball to Pi"
sshpass -p "$SSHPASS_VALUE" ssh "${ssh_common[@]}" "$PI_USER@$PI_HOST" "tar -xzf /tmp/pi_p25_v0_4g.tar.gz -C /tmp/pi_p25_v0_4g && bash /tmp/pi_p25_v0_4g/remote_apply.sh '$PI_REPO'"
pass "remote apply completed"

sshpass -p "$SSHPASS_VALUE" ssh "${ssh_common[@]}" "$PI_USER@$PI_HOST" "python3 - <<'PY'
import json, urllib.request
for url in ['http://127.0.0.1:8070/', 'http://127.0.0.1:8070/api/status', 'http://127.0.0.1:8070/api/config/named']:
    with urllib.request.urlopen(url, timeout=3) as r:
        data = r.read(256)
    print('PROBE_PASS', url, len(data))
PY"
pass "remote HTTP probes passed"
