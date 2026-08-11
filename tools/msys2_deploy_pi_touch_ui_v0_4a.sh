#!/usr/bin/env bash
# Deploy V0.4A touch UI files to the Pi from MSYS2.
# V0.4A1: wait for backend restart and capture service diagnostics before failing.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
REPORT_FILE="$LOG_DIR/deploy_touch_ui_v0_4a_${STAMP}.txt"
TMP_TARBALL="/tmp/pi_p25_v0_4a_touch_ui_${STAMP}.tgz"
REMOTE_TARBALL="/tmp/pi_p25_v0_4a_touch_ui_${STAMP}.tgz"
REMOTE_SCRIPT_LOCAL="/tmp/pi_p25_v0_4a_remote_install_${STAMP}.sh"
REMOTE_SCRIPT="/tmp/pi_p25_v0_4a_remote_install_${STAMP}.sh"
PI_HOST_ARG=""
PI_USER_ARG=""
PI_REPO_ARG=""

mkdir -p "$LOG_DIR"
: > "$REPORT_FILE"
pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
finish() {
  local windows_path
  windows_path="$(cygpath -w "$REPORT_FILE" 2>/dev/null || printf '%s' "$REPORT_FILE")"
  printf 'UPLOAD_FILE_MSYS=%s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
  printf 'UPLOAD_FILE_WINDOWS=%s\n' "$windows_path" | tee -a "$REPORT_FILE"
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"; exit 0; fi
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"; exit 1
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish; fi' ERR

usage() {
  cat <<USAGE
Usage:
  ./tools/msys2_deploy_pi_touch_ui_v0_4a.sh [--host PI-SDR] [--user pi] [--repo /home/pi/n0jcg-scanner]
USAGE
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) shift; PI_HOST_ARG="$1"; shift ;;
    --user) shift; PI_USER_ARG="$1"; shift ;;
    --repo) shift; PI_REPO_ARG="$1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

printf '=== scanner V0.4A touch UI deploy ===\n' | tee -a "$REPORT_FILE"
printf 'Started UTC: %s\n' "$STAMP" | tee -a "$REPORT_FILE"
printf 'Working directory: %s\n' "$(pwd)" | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "web" && -d "tools" ]]; then pass "running from repository root"; else fail "run from repository root"; finish; fi
case "$(uname -s 2>/dev/null || true)" in MINGW*|MSYS*) pass "MSYS2 shell detected" ;; *) warn "shell does not look like MSYS2" ;; esac

if [[ -f .env ]]; then set -a; . ./.env; set +a; pass "loaded .env"; else warn ".env not found; defaults will be used and password may be required"; fi
PI_HOST="${PI_HOST_ARG:-${PI_HOST:-PI-SDR}}"
PI_USER="${PI_USER_ARG:-${PI_USER:-pi}}"
PI_REPO="${PI_REPO_ARG:-${PI_REPO:-/home/pi/n0jcg-scanner}}"
if [[ -z "${PI_PASSWORD:-}" && -n "${SSHPASS:-}" ]]; then PI_PASSWORD="$SSHPASS"; fi
if [[ -z "${PI_PASSWORD:-}" ]]; then read -r -s -p "Pi password for ${PI_USER}@${PI_HOST}: " PI_PASSWORD; echo; fi
if [[ -z "${PI_PASSWORD:-}" ]]; then fail "empty Pi password"; finish; fi
export PI_PASSWORD
pass "Pi connection settings loaded for ${PI_USER}@${PI_HOST}:${PI_REPO}"

for cmd in sshpass ssh scp tar python3 base64; do
  if command -v "$cmd" >/dev/null 2>&1; then pass "command available: $cmd"; else fail "missing required command: $cmd"; fi
done
if [[ "$FAIL_COUNT" -ne 0 ]]; then finish; fi

required_files=(
  web/index.html
  web/app.css
  web/app.js
  web/system_catalog.example.json
  docs/TOUCH_UI_V0_4.md
)
for path in "${required_files[@]}"; do
  if [[ -f "$path" ]]; then pass "deploy file exists: $path"; else fail "missing deploy file: $path"; fi
done
if [[ "$FAIL_COUNT" -ne 0 ]]; then finish; fi

if command -v node >/dev/null 2>&1; then
  if node --check web/app.js >>"$REPORT_FILE" 2>&1; then pass "web/app.js node syntax passed"; else fail "web/app.js node syntax failed"; fi
else
  warn "node unavailable; skipped app.js syntax check"
fi
python3 -m json.tool web/system_catalog.example.json >/dev/null && pass "system catalog JSON valid" || fail "system catalog JSON invalid"
if [[ "$FAIL_COUNT" -ne 0 ]]; then finish; fi

tar -czf "$TMP_TARBALL" "${required_files[@]}"
pass "created deploy tarball: $TMP_TARBALL"

REMOTE_PASSWORD_B64="$(printf '%s' "$PI_PASSWORD" | base64 | tr -d '\n')"
cat > "$REMOTE_SCRIPT_LOCAL" <<REMOTE
#!/usr/bin/env bash
set -Eeuo pipefail
REPO='$PI_REPO'
TARBALL='$REMOTE_TARBALL'
export SUDO_PASSWORD="\$(printf '%s' '$REMOTE_PASSWORD_B64' | base64 -d)"
printf 'Remote deploy repo: %s\n' "\$REPO"
if [[ ! -d "\$REPO" ]]; then printf 'FAIL: repo directory missing: %s\n' "\$REPO" >&2; exit 10; fi
cd "\$REPO"
mkdir -p ".deploy_backups/v0_4a_${STAMP}"
for path in web/index.html web/app.css web/app.js web/system_catalog.example.json docs/TOUCH_UI_V0_4.md; do
  if [[ -f "\$path" ]]; then mkdir -p ".deploy_backups/v0_4a_${STAMP}/\$(dirname "\$path")"; cp -p "\$path" ".deploy_backups/v0_4a_${STAMP}/\$path"; fi
done
tar -xzf "\$TARBALL"
if command -v node >/dev/null 2>&1; then node --check web/app.js; else python3 - <<'PY'
from pathlib import Path
text = Path('web/app.js').read_text(encoding='utf-8')
assert 'startScannerAndAudio' in text
assert 'wizardSystemSelect' in text
print('WARN: node unavailable on Pi; basic app.js marker check passed')
PY
fi
python3 -m json.tool web/system_catalog.example.json >/dev/null
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/backend_launch.py
printf 'PASS: backend python compile passed\n'
if systemctl list-unit-files pi-p25-scanner.service >/dev/null 2>&1; then
  printf '%s\n' "\$SUDO_PASSWORD" | sudo -S systemctl restart pi-p25-scanner.service
  printf 'PASS: restarted pi-p25-scanner.service\n'
else
  printf 'WARN: pi-p25-scanner.service not found; backend not restarted\n'
fi
python3 - <<'PY'
import json
import subprocess
import sys
import time
import urllib.request

urls = ('http://127.0.0.1:8070/', 'http://127.0.0.1:8070/api/status')
last_error = None
for attempt in range(1, 46):
    all_ok = True
    details = []
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                body = resp.read(600).decode('utf-8', errors='replace')
            details.append(('PROBE_OK', url, body[:220].replace('\n', ' ')))
        except Exception as exc:
            all_ok = False
            last_error = f'{url}: {type(exc).__name__}: {exc}'
            break
    if all_ok:
        print(f'PASS: backend responded after {attempt} second(s)')
        for item in details:
            print(item[0], item[1], item[2])
        break
    time.sleep(1)
else:
    print('FAIL: backend did not respond on port 8070 after 45 seconds')
    if last_error:
        print('LAST_PROBE_ERROR', last_error)
    for cmd in (
        ['systemctl', '--no-pager', '-l', 'status', 'pi-p25-scanner.service'],
        ['journalctl', '-u', 'pi-p25-scanner.service', '-n', '160', '--no-pager'],
        ['python3', '-m', 'py_compile', 'src/pi_p25_scanner/backend.py', 'src/pi_p25_scanner/backend_launch.py'],
    ):
        print('DIAG_CMD', ' '.join(cmd))
        try:
            completed = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=12)
            print(completed.stdout[-12000:])
            print('DIAG_RC', completed.returncode)
        except Exception as exc:
            print('DIAG_ERROR', type(exc).__name__, exc)
    sys.exit(20)

# Audio service is useful but should not fail this UI deploy.
try:
    with urllib.request.urlopen('http://127.0.0.1:8072/api/audio/status', timeout=2) as resp:
        data = json.loads(resp.read().decode('utf-8', errors='replace'))
    print('AUDIO_PROBE_OK', json.dumps({k: data.get(k) for k in ('ok', 'mode', 'audio_packets', 'stream_clients')}, sort_keys=True))
except Exception as exc:
    print('AUDIO_PROBE_WARN', type(exc).__name__, exc)
PY
LAN_IP="\$(hostname -I 2>/dev/null | awk '{print \$1}' || true)"
if [[ -n "\$LAN_IP" ]]; then
  printf 'LAN_UI_URL=http://%s:8070\n' "\$LAN_IP"
  printf 'LAN_AUDIO_URL=http://%s:8072/audio.wav\n' "\$LAN_IP"
fi
REMOTE
chmod +x "$REMOTE_SCRIPT_LOCAL"
pass "created remote install script: $REMOTE_SCRIPT_LOCAL"

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -o PreferredAuthentications=password,keyboard-interactive,publickey)
SSH=(sshpass -p "$PI_PASSWORD" ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}")
SCP=(sshpass -p "$PI_PASSWORD" scp -O "${SSH_OPTS[@]}")

"${SCP[@]}" "$TMP_TARBALL" "${PI_USER}@${PI_HOST}:${REMOTE_TARBALL}" >>"$REPORT_FILE" 2>&1
pass "copied deploy tarball to ${PI_USER}@${PI_HOST}:${REMOTE_TARBALL}"
"${SCP[@]}" "$REMOTE_SCRIPT_LOCAL" "${PI_USER}@${PI_HOST}:${REMOTE_SCRIPT}" >>"$REPORT_FILE" 2>&1
pass "copied remote install script to ${PI_USER}@${PI_HOST}:${REMOTE_SCRIPT}"
"${SSH[@]}" "bash '$REMOTE_SCRIPT'" 2>&1 | tee -a "$REPORT_FILE"
pass "remote V0.4A touch UI deploy completed"
"${SSH[@]}" "rm -f '$REMOTE_TARBALL' '$REMOTE_SCRIPT'" >>"$REPORT_FILE" 2>&1 || warn "remote cleanup failed"
rm -f "$TMP_TARBALL" "$REMOTE_SCRIPT_LOCAL" || true
pass "local temporary files cleaned"

printf '\nOpen: http://%s:8070\n' "$PI_HOST" | tee -a "$REPORT_FILE"
finish
