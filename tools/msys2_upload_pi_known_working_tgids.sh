#!/usr/bin/env bash
# Upload and apply the V0.4F known-working TOPAZ/TRWC Mesa specific TGID profile to the Pi.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
if [[ ! -d /c/Users/jim/Downloads ]]; then
  LOG_DIR="${PWD}/.p25_command_logs"
fi
REPORT_FILE="$LOG_DIR/upload_known_working_tgids_v0_4f_${STAMP}.txt"
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
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
    exit 0
  fi
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "upload aborted unexpectedly at line $LINENO rc=$rc"; finish; fi' ERR

PI_HOST_ARG=""
PI_USER_ARG=""
PI_REPO_ARG=""
PROFILE="exact"

usage() {
  cat <<USAGE
Usage:
  ./tools/msys2_upload_pi_known_working_tgids.sh [--profile exact|clear] [--host PI-SDR] [--user pi] [--repo /home/pi/PI-P25-SCANNER]

Profiles:
  exact  Restore the original known-working specific TGID list, including encrypted/problem TGIDs enabled.
  clear  Use the same specific clear/interoperability TGIDs, but keep known encrypted/problem TGIDs disabled.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) shift; PROFILE="${1:-}"; shift ;;
    --host) shift; PI_HOST_ARG="${1:-}"; shift ;;
    --user) shift; PI_USER_ARG="${1:-}"; shift ;;
    --repo) shift; PI_REPO_ARG="${1:-}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

printf '=== PI-P25-SCANNER V0.4F known-working TGID upload ===\n' | tee -a "$REPORT_FILE"
printf 'Started UTC: %s\n' "$STAMP" | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "config/templates" && -d "src/pi_p25_scanner" ]]; then
  pass "running from PI-P25-SCANNER repository root"
else
  fail "run from PI-P25-SCANNER repository root"
  finish
fi

case "$PROFILE" in
  exact)
    TEMPLATE="config/templates/topaz_trwc_mesa_known_working_specific.json"
    ;;
  clear|clear-preferred)
    PROFILE="clear"
    TEMPLATE="config/templates/topaz_trwc_mesa_clear_preferred_specific.json"
    ;;
  *)
    fail "unknown profile: $PROFILE"
    finish
    ;;
esac

if [[ -f "$TEMPLATE" ]]; then
  pass "selected template: $TEMPLATE"
else
  fail "missing template: $TEMPLATE"
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
  pass "loaded .env"
else
  warn ".env not found; will use defaults and may prompt if PI_PASSWORD/SSHPASS is missing"
fi

export PI_HOST="${PI_HOST_ARG:-${PI_HOST:-PI-SDR}}"
export PI_USER="${PI_USER_ARG:-${PI_USER:-pi}}"
export PI_REPO="${PI_REPO_ARG:-${PI_REPO:-/home/pi/PI-P25-SCANNER}}"
export PI_PASSWORD="${PI_PASSWORD:-${SSHPASS:-}}"

if [[ -z "${PI_PASSWORD:-}" ]]; then
  read -r -s -p "Pi password for ${PI_USER}@${PI_HOST}: " PI_PASSWORD
  echo
  export PI_PASSWORD
fi
if [[ -z "${PI_PASSWORD:-}" ]]; then
  fail "empty Pi password"
fi
pass "Pi target: ${PI_USER}@${PI_HOST}:${PI_REPO}"

for cmd in sshpass ssh scp python3 base64; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "command available: $cmd"
  else
    fail "missing required command: $cmd"
  fi
done
if [[ "$FAIL_COUNT" -ne 0 ]]; then finish; fi

python3 -m json.tool "$TEMPLATE" >/dev/null
pass "selected template JSON is valid"

REMOTE_TEMPLATE="/tmp/pi_p25_known_working_${PROFILE}_${STAMP}.json"
REMOTE_SCRIPT_LOCAL="/tmp/pi_p25_apply_known_working_${PROFILE}_${STAMP}.sh"
REMOTE_SCRIPT="/tmp/pi_p25_apply_known_working_${PROFILE}_${STAMP}.sh"
PASSWORD_B64="$(printf '%s' "$PI_PASSWORD" | base64 | tr -d '\n')"

cat > "$REMOTE_SCRIPT_LOCAL" <<REMOTE
#!/usr/bin/env bash
set -Eeuo pipefail
REPO='$PI_REPO'
REMOTE_TEMPLATE='$REMOTE_TEMPLATE'
PROFILE='$PROFILE'
PASSWORD_B64='$PASSWORD_B64'
export SUDO_PASSWORD="\$(printf '%s' "\$PASSWORD_B64" | base64 -d)"
printf 'Remote repo: %s\n' "\$REPO"
cd "\$REPO"
mkdir -p runtime/settings/backups runtime/op25
if [[ -f runtime/settings/p25_systems.json ]]; then
  cp -p runtime/settings/p25_systems.json "runtime/settings/backups/p25_systems_before_v0_4f_\$(date -u +%Y%m%dT%H%M%SZ).json"
  printf 'PASS: backed up current runtime config\n'
fi
python3 -m json.tool "\$REMOTE_TEMPLATE" >/dev/null
cp "\$REMOTE_TEMPLATE" runtime/settings/p25_systems.json
chmod 0644 runtime/settings/p25_systems.json
printf 'PASS: applied %s profile to runtime/settings/p25_systems.json\n' "\$PROFILE"
PYTHONPATH=src python3 -m pi_p25_scanner.op25_config --config runtime/settings/p25_systems.json --output runtime/op25 --json > runtime/op25/known_working_v0_4f_manifest.json
printf 'PASS: regenerated OP25 runtime files under runtime/op25\n'
python3 - <<'PY'
import json
from pathlib import Path
cfg = json.loads(Path('runtime/settings/p25_systems.json').read_text())
sys = cfg['systems'][0]
enabled = [tg for tg in sys.get('talkgroups', []) if tg.get('enabled', True)]
disabled = [tg for tg in sys.get('talkgroups', []) if not tg.get('enabled', True)]
print('CONFIG_NAME=' + sys.get('name', ''))
print('CONTROL_CHANNELS_MHZ=' + ','.join(f"{x/1000000:.6f}" for x in sys.get('control_channels_hz', [])))
print('VOICE_CHANNEL_COUNT=' + str(len(sys.get('voice_channels_hz', []))))
print('ENABLED_TGIDS=' + ','.join(str(tg.get('tgid')) for tg in enabled))
print('DISABLED_TGIDS=' + ','.join(str(tg.get('tgid')) for tg in disabled))
PY
if systemctl list-unit-files pi-p25-scanner.service >/dev/null 2>&1; then
  printf '%s\n' "\$SUDO_PASSWORD" | sudo -S systemctl restart pi-p25-scanner.service
  printf 'PASS: restarted pi-p25-scanner.service\n'
else
  printf 'WARN: pi-p25-scanner.service unit not found; not restarted\n'
fi
python3 - <<'PY'
import json
import urllib.request
for url in ('http://127.0.0.1:8070/api/config', 'http://127.0.0.1:8070/api/status'):
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read(2000).decode('utf-8', errors='replace')
        print('PROBE_OK', url, body[:500].replace('\n', ' '))
    except Exception as exc:
        print('PROBE_WARN', url, type(exc).__name__, exc)
PY
LAN_IP="\$(hostname -I 2>/dev/null | awk '{print \$1}' || true)"
if [[ -n "\$LAN_IP" ]]; then
  printf 'PI_DASHBOARD=http://%s:8070\n' "\$LAN_IP"
else
  printf 'PI_DASHBOARD=http://PI-SDR:8070\n'
fi
rm -f "\$REMOTE_TEMPLATE"
REMOTE
chmod +x "$REMOTE_SCRIPT_LOCAL"
pass "created remote apply script: $REMOTE_SCRIPT_LOCAL"

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -o PreferredAuthentications=password,keyboard-interactive,publickey)
SSH=(sshpass -p "$PI_PASSWORD" ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}")
SCP=(sshpass -p "$PI_PASSWORD" scp -O "${SSH_OPTS[@]}")

"${SCP[@]}" "$TEMPLATE" "${PI_USER}@${PI_HOST}:${REMOTE_TEMPLATE}" >>"$REPORT_FILE" 2>&1
pass "uploaded template to ${PI_HOST}:${REMOTE_TEMPLATE}"
"${SCP[@]}" "$REMOTE_SCRIPT_LOCAL" "${PI_USER}@${PI_HOST}:${REMOTE_SCRIPT}" >>"$REPORT_FILE" 2>&1
pass "uploaded remote apply script to ${PI_HOST}:${REMOTE_SCRIPT}"
"${SSH[@]}" "bash '$REMOTE_SCRIPT'" 2>&1 | tee -a "$REPORT_FILE"
pass "remote apply completed"
"${SSH[@]}" "rm -f '$REMOTE_SCRIPT'" >>"$REPORT_FILE" 2>&1 || warn "remote cleanup failed"
rm -f "$REMOTE_SCRIPT_LOCAL" || true
pass "local temporary files cleaned"

printf '\nNext steps:\n' | tee -a "$REPORT_FILE"
printf '  1. Open http://%s:8070\n' "$PI_HOST" | tee -a "$REPORT_FILE"
printf '  2. Stop Scanner if running\n' | tee -a "$REPORT_FILE"
printf '  3. Tap Start Scanner + Audio\n' | tee -a "$REPORT_FILE"
printf '  4. If encrypted groups dominate, rerun with: --profile clear\n' | tee -a "$REPORT_FILE"
finish
