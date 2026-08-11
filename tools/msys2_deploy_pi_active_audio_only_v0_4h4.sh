#!/usr/bin/env bash
set -Eeuo pipefail
VERSION="v0_4h4"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/deploy_${VERSION}_active_audio_only_${STAMP}.txt"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
exec > >(tee -a "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
finish(){ local rc="$1"; echo "UPLOAD_FILE_MSYS=$LOG_FILE"; echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"; echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; [[ "$rc" -eq 0 ]] && echo "FINAL: PASS" || echo "FINAL: FAIL"; }
trap 'rc=$?; fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish "$rc"; exit "$rc"' ERR

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"
[[ -f src/pi_p25_scanner/backend.py && -f src/pi_p25_scanner/runtime_status.py ]] || { fail "run from repo root"; finish 1; exit 1; }
pass "repo root detected: $ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-192.168.254.63}"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
SSHPASS_VALUE="${SSHPASS:-${PI_PASSWORD:-}}"
if [[ -z "$SSHPASS_VALUE" ]]; then
  read -r -s -p "Password for ${PI_USER}@${PI_HOST}: " SSHPASS_VALUE
  echo
fi
command -v sshpass >/dev/null 2>&1 || { fail "sshpass is required in MSYS2"; finish 1; exit 1; }
pass "deploy target: ${PI_USER}@${PI_HOST}:${PI_REPO}"

python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/runtime_status.py
pass "local python compile passed"

git --no-pager diff --check -- src/pi_p25_scanner/backend.py src/pi_p25_scanner/runtime_status.py
pass "local git diff --check passed"

TARBALL="/tmp/pi_p25_${VERSION}_active_audio_only_${STAMP}.tar.gz"
tar -czf "$TARBALL" src/pi_p25_scanner/backend.py src/pi_p25_scanner/runtime_status.py
pass "created deploy tarball: $TARBALL"

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
sshpass -p "$SSHPASS_VALUE" scp -O "${SSH_OPTS[@]}" "$TARBALL" "${PI_USER}@${PI_HOST}:/tmp/$(basename "$TARBALL")"
pass "copied deploy tarball to ${PI_HOST}"

REMOTE_TARBALL="/tmp/$(basename "$TARBALL")"
sshpass -p "$SSHPASS_VALUE" ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}" bash -s <<REMOTE
set -Eeuo pipefail
cd "$PI_REPO"
BACKUP_DIR="runtime/patch_backups/${VERSION}_deploy_${STAMP}"
mkdir -p "\$BACKUP_DIR"
cp src/pi_p25_scanner/backend.py "\$BACKUP_DIR/backend.py.bak" 2>/dev/null || true
cp src/pi_p25_scanner/runtime_status.py "\$BACKUP_DIR/runtime_status.py.bak" 2>/dev/null || true
tar -xzf "$REMOTE_TARBALL" -C "$PI_REPO"
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/runtime_status.py
sudo systemctl restart pi-p25-scanner.service
sleep 2
REMOTE
pass "remote files installed, compiled, and service restart requested"

python3 - <<PY
import json, sys, time, urllib.request
base = "http://${PI_HOST}:8070"
last = None
for _ in range(25):
    try:
        with urllib.request.urlopen(base + "/api/status", timeout=3) as r:
            status = json.loads(r.read().decode("utf-8"))
        with urllib.request.urlopen(base + "/api/activity", timeout=3) as r:
            activity = json.loads(r.read().decode("utf-8"))
        print("STATUS_PROBE_OK", json.dumps({
            "scanner_state": status.get("scanner_state"),
            "active_tgid": status.get("active_tgid"),
            "activity_active_tgid": activity.get("active_tgid"),
            "encrypted": activity.get("encrypted"),
            "muted": activity.get("muted"),
        }, sort_keys=True))
        sys.exit(0)
    except Exception as exc:
        last = repr(exc)
        time.sleep(1)
print("STATUS_PROBE_FAIL", last)
sys.exit(1)
PY
pass "probed /api/status and /api/activity"

cat > tools/msys2_probe_pi_active_audio_only_v0_4h4.sh <<'PROBE'
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ -f .env ]]; then set -a; source .env; set +a; fi
PI_HOST="${PI_HOST:-192.168.254.63}"
python3 - <<PY
import json, time, urllib.request
base = "http://${PI_HOST}:8070"
for i in range(40):
    try:
        with urllib.request.urlopen(base + "/api/activity", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        print(json.dumps({
            "i": i,
            "scanner_state": data.get("scanner_state"),
            "active_tgid": data.get("active_tgid"),
            "label": data.get("active_talkgroup_label"),
            "voice_hz": data.get("active_voice_frequency_hz"),
            "encrypted": data.get("encrypted"),
            "muted": data.get("muted"),
            "last_event": data.get("last_event"),
        }, sort_keys=True))
    except Exception as exc:
        print("PROBE_ERROR", repr(exc))
    time.sleep(0.5)
PY
PROBE
chmod +x tools/msys2_probe_pi_active_audio_only_v0_4h4.sh
pass "wrote active-audio-only probe helper"

finish 0
