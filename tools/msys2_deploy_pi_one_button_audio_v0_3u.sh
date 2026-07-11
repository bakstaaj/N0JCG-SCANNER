#!/usr/bin/env bash
# Deploy V0.3U one-button browser-audio UI and OP25 UDP launch helper to the Pi.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
REPORT_FILE="$LOG_DIR/deploy_one_button_audio_v0_3u_${STAMP}.txt"
TMP_TARBALL="/tmp/pi_p25_v0_3u_one_button_audio_${STAMP}.tgz"
REMOTE_TARBALL="/tmp/pi_p25_v0_3u_one_button_audio_${STAMP}.tgz"
REMOTE_SCRIPT_LOCAL="/tmp/pi_p25_v0_3u_remote_install_${STAMP}.sh"
REMOTE_SCRIPT="/tmp/pi_p25_v0_3u_remote_install_${STAMP}.sh"
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
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
    exit 0
  fi
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish; fi' ERR

usage() {
  cat <<USAGE
Usage:
  ./tools/msys2_deploy_pi_one_button_audio_v0_3u.sh [--host PI-SDR] [--user pi] [--repo /home/pi/PI-P25-SCANNER]
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

printf '=== PI-P25-SCANNER V0.3U one-button audio deploy ===\n' | tee -a "$REPORT_FILE"
printf 'Started UTC: %s\n' "$STAMP" | tee -a "$REPORT_FILE"
printf 'Working directory: %s\n' "$(pwd)" | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "web" ]]; then
  pass "running from PI-P25-SCANNER repository root"
else
  fail "run from PI-P25-SCANNER repository root"
  finish
fi
case "$(uname -s 2>/dev/null || true)" in
  MINGW*|MSYS*) pass "MSYS2 shell detected" ;;
  *) warn "shell does not look like MSYS2" ;;
esac

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . .env
  set +a
  pass "loaded .env"
else
  warn ".env not found; using defaults plus environment variables"
fi
export PI_HOST="${PI_HOST_ARG:-${PI_HOST:-PI-SDR}}"
export PI_USER="${PI_USER_ARG:-${PI_USER:-pi}}"
export PI_REPO="${PI_REPO_ARG:-${PI_REPO:-/home/pi/PI-P25-SCANNER}}"
if [[ -z "${PI_PASSWORD:-}" && -n "${SSHPASS:-}" ]]; then
  export PI_PASSWORD="$SSHPASS"
fi
if [[ -z "${PI_PASSWORD:-}" ]]; then
  fail "PI_PASSWORD is missing; add it to .env or export PI_PASSWORD"
  finish
fi
pass "Pi connection settings loaded for ${PI_USER}@${PI_HOST}:${PI_REPO}"

for cmd in sshpass ssh scp tar python3 node; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "command available: $cmd"
  else
    fail "missing required command: $cmd"
  fi
done
if [[ "$FAIL_COUNT" -ne 0 ]]; then finish; fi

if python3 -m py_compile src/pi_p25_scanner/backend_launch.py >>"$REPORT_FILE" 2>&1; then
  pass "backend_launch python compile passed"
else
  fail "backend_launch python compile failed"
fi
if node --check web/app.js >>"$REPORT_FILE" 2>&1; then
  pass "web/app.js node syntax passed"
else
  fail "web/app.js node syntax failed"
fi
if [[ "$FAIL_COUNT" -ne 0 ]]; then finish; fi

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -o PreferredAuthentications=password,keyboard-interactive,publickey)
SSH=(sshpass -p "$PI_PASSWORD" ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}")
SCP=(sshpass -p "$PI_PASSWORD" scp -O "${SSH_OPTS[@]}")

required_files=(
  src/pi_p25_scanner/backend_launch.py
  web/index.html
  web/app.js
)
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
if [[ ! -d "\$REPO" ]]; then
  printf 'FAIL: repo directory missing: %s\n' "\$REPO" >&2
  exit 10
fi
cd "\$REPO"
tar -xzf "\$TARBALL"
python3 -m py_compile src/pi_p25_scanner/backend_launch.py
if systemctl list-unit-files pi-p25-browser-audio-raw.service >/dev/null 2>&1; then
  printf '%s\n' "\$SUDO_PASSWORD" | sudo -S systemctl restart pi-p25-browser-audio-raw.service
  printf 'PASS: restarted pi-p25-browser-audio-raw.service\n'
else
  printf 'WARN: pi-p25-browser-audio-raw.service unit not found; run V0.3T repair if audio service is missing\n'
fi
if systemctl list-unit-files pi-p25-scanner.service >/dev/null 2>&1; then
  printf '%s\n' "\$SUDO_PASSWORD" | sudo -S systemctl restart pi-p25-scanner.service
  printf 'PASS: restarted pi-p25-scanner.service\n'
else
  printf 'WARN: pi-p25-scanner.service unit not found; backend not restarted\n'
fi
python3 - <<'PY'
import json
import urllib.request
checks = [
    ('dashboard', 'http://127.0.0.1:8070/'),
    ('status', 'http://127.0.0.1:8070/api/status'),
    ('audio_status', 'http://127.0.0.1:8072/api/audio/status'),
    ('audio_tone', 'http://127.0.0.1:8072/test-tone.wav'),
]
for name, url in checks:
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            body = resp.read(400)
        print('PROBE_OK', name, url, len(body))
    except Exception as exc:
        print('PROBE_WARN', name, url, type(exc).__name__, exc)
PY
REMOTE
chmod +x "$REMOTE_SCRIPT_LOCAL"
pass "created remote install script: $REMOTE_SCRIPT_LOCAL"

"${SCP[@]}" "$TMP_TARBALL" "${PI_USER}@${PI_HOST}:${REMOTE_TARBALL}" >>"$REPORT_FILE" 2>&1
pass "copied deploy tarball to ${PI_USER}@${PI_HOST}:${REMOTE_TARBALL}"
"${SCP[@]}" "$REMOTE_SCRIPT_LOCAL" "${PI_USER}@${PI_HOST}:${REMOTE_SCRIPT}" >>"$REPORT_FILE" 2>&1
pass "copied remote install script to ${PI_USER}@${PI_HOST}:${REMOTE_SCRIPT}"

"${SSH[@]}" "bash '$REMOTE_SCRIPT'" 2>&1 | tee -a "$REPORT_FILE"
pass "remote one-button audio deploy completed"

"${SSH[@]}" "rm -f '$REMOTE_TARBALL' '$REMOTE_SCRIPT'" >>"$REPORT_FILE" 2>&1 || warn "remote cleanup failed"
rm -f "$TMP_TARBALL" "$REMOTE_SCRIPT_LOCAL" || true
pass "local temporary files cleaned"

printf '\nNext test:\n' | tee -a "$REPORT_FILE"
printf '  Open http://%s:8070\n' "$PI_HOST" | tee -a "$REPORT_FILE"
printf '  Hard refresh, then click Start Scanner + Browser Audio\n' | tee -a "$REPORT_FILE"
finish
