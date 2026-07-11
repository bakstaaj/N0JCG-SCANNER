#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="deploy_v0_4h2b_fast_activity"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/${SCRIPT_NAME}_${STAMP}.txt"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
log(){ printf '%s\n' "$*" | tee -a "$LOG_FILE"; }
pass(){ PASS_COUNT=$((PASS_COUNT+1)); log "PASS: $*"; }
warn(){ WARN_COUNT=$((WARN_COUNT+1)); log "WARN: $*"; }
fail(){ FAIL_COUNT=$((FAIL_COUNT+1)); log "FAIL: $*"; }
finish(){
  local rc=$?
  if (( rc != 0 )); then fail "deploy aborted unexpectedly at line ${BASH_LINENO[0]:-?} rc=$rc"; fi
  log "UPLOAD_FILE_MSYS=$LOG_FILE"
  log "UPLOAD_FILE_WINDOWS=$(printf '%s' "$LOG_FILE" | sed 's#^/c/#C:\\\\#; s#/#\\\\#g')"
  log "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if (( FAIL_COUNT == 0 && rc == 0 )); then log "FINAL: PASS"; else log "FINAL: FAIL"; fi
  exit $rc
}
trap finish EXIT

log "=== Deploy V0.4H2B fast talkgroup activity ==="

if [[ -d .git && -f src/pi_p25_scanner/backend.py && -f web/app.js ]]; then
  REPO_ROOT="$PWD"
elif [[ -d "$HOME/sdrdev/PI-P25-SCANNER/.git" ]]; then
  REPO_ROOT="$HOME/sdrdev/PI-P25-SCANNER"
else
  fail "could not locate PI-P25-SCANNER repo root"
  exit 1
fi
cd "$REPO_ROOT"
pass "repo root detected: $REPO_ROOT"

[[ -f .env ]] && set -a && source ./.env && set +a || true
PI_HOST="${PI_HOST:-192.168.254.63}"
PI_USER="${PI_USER:-pi}"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
PI_PASSWORD="${PI_PASSWORD:-${SSHPASS:-}}"
pass "deploy target: ${PI_USER}@${PI_HOST}:${PI_REPO}"

for f in src/pi_p25_scanner/backend.py web/app.js; do
  [[ -f "$f" ]] || { fail "required file missing: $f"; exit 1; }
done
pass "deploy files present"

python3 -m py_compile src/pi_p25_scanner/backend.py
pass "backend.py local python compile passed"
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
  pass "web/app.js local node syntax passed"
else
  warn "node unavailable locally; skipped app.js syntax check"
fi

git diff --check
pass "local git diff --check passed"

if [[ -n "$PI_PASSWORD" ]]; then
  SSH=(sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  SCP=(sshpass -p "$PI_PASSWORD" scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
else
  warn "PI_PASSWORD/SSHPASS not set; using ssh/scp without sshpass"
  SSH=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
  SCP=(scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
fi

TARBALL="/tmp/pi_p25_v0_4h2b_fast_activity_${STAMP}.tar.gz"
tar -czf "$TARBALL" src/pi_p25_scanner/backend.py web/app.js
pass "created deploy tarball: $TARBALL"

"${SCP[@]}" "$TARBALL" "${PI_USER}@${PI_HOST}:/tmp/$(basename "$TARBALL")"
pass "copied deploy tarball to ${PI_HOST}"

REMOTE_TARBALL="/tmp/$(basename "$TARBALL")"
"${SSH[@]}" "${PI_USER}@${PI_HOST}" bash -s -- "$PI_REPO" "$REMOTE_TARBALL" <<'REMOTE'
set -Eeuo pipefail
PI_REPO="$1"
REMOTE_TARBALL="$2"
cd "$PI_REPO"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="runtime/patch_backups/deploy_v0_4h2b_${STAMP}"
mkdir -p "$BACKUP_DIR"
cp -p src/pi_p25_scanner/backend.py "$BACKUP_DIR/backend.py.bak" 2>/dev/null || true
cp -p web/app.js "$BACKUP_DIR/app.js.bak" 2>/dev/null || true
tar -xzf "$REMOTE_TARBALL"
python3 -m py_compile src/pi_p25_scanner/backend.py
if command -v node >/dev/null 2>&1; then node --check web/app.js; fi
sudo systemctl restart pi-p25-scanner.service
sleep 2
REMOTE
pass "remote files installed, compiled, and service restart requested"

probe_remote(){
  local path="$1"
  "${SSH[@]}" "${PI_USER}@${PI_HOST}" python3 - "$path" <<'PY'
import json, sys, time, urllib.request, urllib.error
path = sys.argv[1]
url = "http://127.0.0.1:8070" + path
last = ""
for _ in range(30):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            body = r.read(512000).decode("utf-8", "replace")
        payload = json.loads(body)
        print(json.dumps({"ok": payload.get("ok"), "path": path, "keys": sorted(payload.keys())[:20]}, sort_keys=True))
        raise SystemExit(0)
    except Exception as exc:
        last = repr(exc)
        time.sleep(1)
print(f"PROBE_FAIL {url} {last}")
raise SystemExit(1)
PY
}

if probe_remote "/api/status"; then
  pass "remote /api/status probe passed"
else
  fail "remote /api/status probe failed"
  "${SSH[@]}" "${PI_USER}@${PI_HOST}" 'systemctl --no-pager status pi-p25-scanner.service || true; journalctl -u pi-p25-scanner.service -n 120 --no-pager || true' | tee -a "$LOG_FILE"
  exit 1
fi

if probe_remote "/api/activity"; then
  pass "remote /api/activity probe passed"
else
  fail "remote /api/activity probe failed"
  "${SSH[@]}" "${PI_USER}@${PI_HOST}" 'systemctl --no-pager status pi-p25-scanner.service || true; journalctl -u pi-p25-scanner.service -n 120 --no-pager || true' | tee -a "$LOG_FILE"
  exit 1
fi

log "Dashboard: http://${PI_HOST}:8070"
