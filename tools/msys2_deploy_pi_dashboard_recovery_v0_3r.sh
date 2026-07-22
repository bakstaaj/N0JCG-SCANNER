#!/usr/bin/env bash
# Deploy V0.3R stable dashboard recovery to the Raspberry Pi and verify /api/status.
# Run from MSYS2 UCRT64 at ~/sdrdev/PI-P25-SCANNER.
set -Eeuo pipefail

HOST="PI-SDR"
USER="pi"
PI_REPO="/home/pi/PI-P25-SCANNER"
PASSWORD="${PI_PASSWORD:-}"
DEST="/c/Users/jim/Downloads/pi-p25-command-logs"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_DIR=".p25_v0_3r_dashboard_recovery_reports"
REPORT_FILE="$REPORT_DIR/v0_3r_deploy_${STAMP}.txt"
mkdir -p "$REPORT_DIR" "$DEST"
: > "$REPORT_FILE"

pass(){ printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; }
warn(){ printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; }
fail(){ printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; }
finish(){ printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"; if [[ "${FAIL:-0}" -eq 0 ]]; then printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"; exit 0; else printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"; exit 1; fi; }
FAIL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) shift; HOST="$1"; shift ;;
    --user) shift; USER="$1"; shift ;;
    --repo) shift; PI_REPO="$1"; shift ;;
    --password) shift; PASSWORD="$1"; shift ;;
    --dest) shift; DEST="$1"; shift ;;
    -h|--help)
      cat <<USAGE
Usage: ./tools/msys2_deploy_pi_dashboard_recovery_v0_3r.sh [--host PI-SDR] [--user pi] [--repo /home/pi/PI-P25-SCANNER]
USAGE
      exit 0
      ;;
    *) fail "unknown option: $1"; FAIL=1; finish ;;
  esac
done

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
  PASSWORD="${PASSWORD:-${PI_PASSWORD:-}}"
fi
if [[ -f tools/msys2_env_common.sh ]]; then
  # shellcheck disable=SC1091
  . tools/msys2_env_common.sh || true
  PASSWORD="${PASSWORD:-${PI_PASSWORD:-}}"
fi

for cmd in git sshpass ssh scp; do
  if command -v "$cmd" >/dev/null 2>&1; then pass "command available: $cmd"; else fail "missing command: $cmd"; FAIL=1; fi
done
if [[ -z "$PASSWORD" ]]; then fail "PI password not found; set PI_PASSWORD in .env or pass --password"; FAIL=1; fi
if [[ "$FAIL" -ne 0 ]]; then finish; fi

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then pass "local git repo detected"; else fail "not in local git repo"; FAIL=1; finish; fi
if git status --porcelain | grep -q .; then
  warn "local tree has uncommitted changes; continuing because recovery may intentionally be staged/committed already"
fi

REMOTE="${USER}@${HOST}"
SSH_BASE=(sshpass -p "$PASSWORD" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$REMOTE")
SCP_BASE=(sshpass -p "$PASSWORD" scp -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)

REMOTE_SCRIPT="/tmp/pi_p25_v0_3r_dashboard_recovery_${STAMP}.sh"
cat > "/tmp/pi_p25_v0_3r_dashboard_recovery_${STAMP}.sh" <<REMOTE
#!/usr/bin/env bash
set -Eeuo pipefail
cd "$PI_REPO"
printf '=== PI-P25-SCANNER V0.3R Pi dashboard recovery deploy ===\\n'
printf 'Working directory: %s\\n' "\$(pwd)"
git fetch --all --prune
git pull --ff-only
python3 -m py_compile src/pi_p25_scanner/backend.py
if command -v node >/dev/null 2>&1; then node --check web/app.js; else echo 'WARN: node missing on Pi; skipped app.js syntax check'; fi
sudo systemctl restart pi-p25-scanner.service
sleep 3
systemctl is-active --quiet pi-p25-scanner.service
printf 'PASS: service active after restart\\n'
python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8070/api/status', timeout=5) as resp:
    data = json.loads(resp.read().decode('utf-8'))
print('PASS: /api/status responded')
print('SCANNER_STATE=' + str(data.get('scanner_state')))
print('LAST_EVENT=' + str(data.get('last_event')))
PY
python3 - <<'PY'
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:8070/', timeout=5) as resp:
    body = resp.read(4096)
print('PASS: UI root responded bytes=' + str(len(body)))
PY
LAN_IP="\$(hostname -I 2>/dev/null | awk '{print \$1}' || true)"
printf 'APP_URL=http://%s:8070\\n' "\${LAN_IP:-PI-SDR}"
printf 'FINAL: PASS\\n'
REMOTE

chmod +x "/tmp/pi_p25_v0_3r_dashboard_recovery_${STAMP}.sh"
"${SCP_BASE[@]}" "/tmp/pi_p25_v0_3r_dashboard_recovery_${STAMP}.sh" "$REMOTE:$REMOTE_SCRIPT" >> "$REPORT_FILE" 2>&1 || { fail "scp remote deploy script failed"; FAIL=1; finish; }
pass "uploaded remote deploy script"

if "${SSH_BASE[@]}" "bash '$REMOTE_SCRIPT'" 2>&1 | tee -a "$REPORT_FILE"; then
  pass "remote deploy/restart/status probe passed"
else
  fail "remote deploy/restart/status probe failed"
  FAIL=1
fi

cp "$REPORT_FILE" "$DEST/$(basename "$REPORT_FILE")" 2>/dev/null || true
printf 'UPLOAD_FILE_MSYS=%s/%s\n' "$DEST" "$(basename "$REPORT_FILE")" | tee -a "$REPORT_FILE"
printf 'UPLOAD_FILE_WINDOWS=%s\\%s\n' "C:\\Users\\jim\\Downloads\\pi-p25-command-logs" "$(basename "$REPORT_FILE")" | tee -a "$REPORT_FILE"
finish
