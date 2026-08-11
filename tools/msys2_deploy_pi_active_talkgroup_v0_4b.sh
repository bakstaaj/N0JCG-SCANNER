#!/usr/bin/env bash
# Deploy V0.4B active talkgroup display changes to the Pi.
set -Eeuo pipefail
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
REPORT_FILE="$LOG_DIR/deploy_active_talkgroup_v0_4b_${STAMP}.txt"
TMP_TARBALL="/tmp/pi_p25_v0_4b_active_talkgroup_${STAMP}.tgz"
REMOTE_TARBALL="/tmp/pi_p25_v0_4b_active_talkgroup_${STAMP}.tgz"
REMOTE_SCRIPT_LOCAL="/tmp/pi_p25_v0_4b_remote_install_${STAMP}.sh"
REMOTE_SCRIPT="/tmp/pi_p25_v0_4b_remote_install_${STAMP}.sh"
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
usage() { printf 'Usage: %s [--host PI-SDR] [--user pi] [--repo /home/pi/n0jcg-scanner]\n' "$0"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) shift; PI_HOST_ARG="$1"; shift ;;
    --user) shift; PI_USER_ARG="$1"; shift ;;
    --repo) shift; PI_REPO_ARG="$1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done
printf '=== scanner V0.4B active talkgroup deploy ===\n' | tee -a "$REPORT_FILE"
printf 'Started UTC: %s\n' "$STAMP" | tee -a "$REPORT_FILE"
printf 'Working directory: %s\n' "$(pwd)" | tee -a "$REPORT_FILE"
if [[ -f DEV_GUARDRAILS.md && -d src/pi_p25_scanner && -d web ]]; then pass "running from repo root"; else fail "run from repo root"; finish; fi
if [[ -f .env ]]; then set -a; . ./.env; set +a; pass "loaded .env"; else warn ".env not found; using defaults and existing environment"; fi
PI_HOST="${PI_HOST_ARG:-${PI_HOST:-PI-SDR}}"
PI_USER="${PI_USER_ARG:-${PI_USER:-pi}}"
PI_REPO="${PI_REPO_ARG:-${PI_REPO:-/home/pi/n0jcg-scanner}}"
if [[ -z "${PI_PASSWORD:-}" && -n "${SSHPASS:-}" ]]; then PI_PASSWORD="$SSHPASS"; fi
if [[ -z "${PI_PASSWORD:-}" ]]; then fail "PI_PASSWORD missing; run one of the earlier env setup scripts or set PI_PASSWORD in .env"; finish; fi
export PI_PASSWORD
pass "Pi target loaded: ${PI_USER}@${PI_HOST}:${PI_REPO}"
for cmd in sshpass ssh scp tar python3; do command -v "$cmd" >/dev/null 2>&1 && pass "command available: $cmd" || fail "missing command: $cmd"; done
if [[ "$FAIL_COUNT" -ne 0 ]]; then finish; fi
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/runtime_activity.py src/pi_p25_scanner/runtime_status.py >>"$REPORT_FILE" 2>&1 && pass "local backend compile passed" || fail "local backend compile failed"
if command -v node >/dev/null 2>&1; then node --check web/app.js >>"$REPORT_FILE" 2>&1 && pass "local app.js syntax passed" || fail "local app.js syntax failed"; else warn "node unavailable locally"; fi
if [[ "$FAIL_COUNT" -ne 0 ]]; then finish; fi
required_files=(src/pi_p25_scanner/backend.py web/index.html web/app.js)
tar -czf "$TMP_TARBALL" "${required_files[@]}"
pass "created deploy tarball: $TMP_TARBALL"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -o PreferredAuthentications=password,keyboard-interactive,publickey)
SSH=(sshpass -p "$PI_PASSWORD" ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}")
SCP=(sshpass -p "$PI_PASSWORD" scp -O "${SSH_OPTS[@]}")
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
tar -xzf "\$TARBALL"
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/runtime_activity.py src/pi_p25_scanner/runtime_status.py
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
else
  grep -q 'function bestTalkgroup' web/app.js
  printf 'WARN: node unavailable on Pi; app.js marker check passed\n'
fi
if systemctl list-unit-files pi-p25-scanner.service >/dev/null 2>&1; then
  printf '%s\n' "\$SUDO_PASSWORD" | sudo -S systemctl restart pi-p25-scanner.service
  printf 'PASS: restarted pi-p25-scanner.service\n'
else
  pkill -f 'src/pi_p25_scanner/backend.py' || true
  nohup python3 src/pi_p25_scanner/backend.py --host 0.0.0.0 --port 8070 > runtime/logs/backend.log 2>&1 &
  printf 'WARN: systemd service missing; started backend with nohup\n'
fi
python3 - <<'PY'
import json
import time
import urllib.request
last_error = None
for _ in range(45):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8070/api/status', timeout=2) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        print('PROBE_OK /api/status', data.get('scanner_state'), data.get('decoder_process', {}).get('running'))
        break
    except Exception as exc:
        last_error = exc
        time.sleep(1)
else:
    print('PROBE_FAIL /api/status', type(last_error).__name__, last_error)
    raise SystemExit(20)
PY
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
printf 'PI_LAN_IP=%s\n' "\$LAN_IP"
REMOTE
chmod +x "$REMOTE_SCRIPT_LOCAL"
pass "created remote install script"
"${SCP[@]}" "$TMP_TARBALL" "${PI_USER}@${PI_HOST}:${REMOTE_TARBALL}" >>"$REPORT_FILE" 2>&1
pass "copied deploy tarball"
"${SCP[@]}" "$REMOTE_SCRIPT_LOCAL" "${PI_USER}@${PI_HOST}:${REMOTE_SCRIPT}" >>"$REPORT_FILE" 2>&1
pass "copied remote install script"
"${SSH[@]}" "bash '$REMOTE_SCRIPT'" 2>&1 | tee -a "$REPORT_FILE"
pass "remote active talkgroup deploy completed"
"${SSH[@]}" "rm -f '$REMOTE_TARBALL' '$REMOTE_SCRIPT'" >>"$REPORT_FILE" 2>&1 || warn "remote cleanup failed"
rm -f "$TMP_TARBALL" "$REMOTE_SCRIPT_LOCAL" || true
pass "temporary files cleaned"
printf '\nOpen: http://%s:8070\n' "$PI_HOST" | tee -a "$REPORT_FILE"
finish
