#!/usr/bin/env bash
# Deploy V0.3S raw browser-audio service files to the Pi using MSYS2 .env + sshpass.
# Self-contained password handling: does not depend on helper function names.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
REPORT_FILE="$LOG_DIR/deploy_raw_audio_service_v0_3s_${STAMP}.txt"
TMP_TARBALL="/tmp/pi_p25_v0_3s_raw_audio_${STAMP}.tgz"
REMOTE_TARBALL="/tmp/pi_p25_v0_3s_raw_audio_${STAMP}.tgz"
REMOTE_SCRIPT_LOCAL="/tmp/pi_p25_v0_3s_remote_install_${STAMP}.sh"
REMOTE_SCRIPT="/tmp/pi_p25_v0_3s_remote_install_${STAMP}.sh"
PI_HOST_ARG=""
PI_USER_ARG=""
PI_REPO_ARG=""
PI_PASSWORD_ARG=""
NO_RESTART_BACKEND=0

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
  ./tools/msys2_deploy_pi_raw_audio_service_v0_3s.sh [--host PI-SDR] [--user pi] [--repo /home/pi/PI-P25-SCANNER] [--password PASSWORD]

Uses .env / PI_PASSWORD with sshpass. If PI_PASSWORD is missing, it prompts once and saves it to .env.
USAGE
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) shift; PI_HOST_ARG="$1"; shift ;;
    --user) shift; PI_USER_ARG="$1"; shift ;;
    --repo) shift; PI_REPO_ARG="$1"; shift ;;
    --password) shift; PI_PASSWORD_ARG="$1"; shift ;;
    --no-restart-backend) NO_RESTART_BACKEND=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

printf '=== PI-P25-SCANNER V0.3S raw audio service deploy ===\n' | tee -a "$REPORT_FILE"
printf 'Started UTC: %s\n' "$STAMP" | tee -a "$REPORT_FILE"
printf 'Working directory: %s\n' "$(pwd)" | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "tools" ]]; then
  pass "running from PI-P25-SCANNER repository root"
else
  fail "run from PI-P25-SCANNER repository root"
  finish
fi
case "$(uname -s 2>/dev/null || true)" in
  MINGW*|MSYS*) pass "MSYS2 shell detected" ;;
  *) warn "shell does not look like MSYS2" ;;
esac

# Load existing helper if present, but do not rely on any specific function names.
if [[ -f "tools/msys2_env_common.sh" ]]; then
  # shellcheck disable=SC1091
  . tools/msys2_env_common.sh
  pass "loaded tools/msys2_env_common.sh"
else
  warn "tools/msys2_env_common.sh missing; using deploy-local .env loader"
fi

# Self-contained .env loader/saver fallback.
p25_deploy_env_file() { printf '%s' "${P25_ENV_FILE:-.env}"; }
p25_deploy_load_dotenv() {
  local env_file
  env_file="$(p25_deploy_env_file)"
  if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$env_file"
    set +a
    pass "loaded local .env: $env_file"
  else
    warn "local .env not found; will prompt if PI_PASSWORD is not provided"
  fi
}
p25_deploy_dotenv_set() {
  local key="$1" value="$2" env_file tmp quoted
  env_file="$(p25_deploy_env_file)"
  mkdir -p "$(dirname "$env_file")"
  touch "$env_file"
  chmod 600 "$env_file" 2>/dev/null || true
  tmp="${env_file}.tmp.$$"
  printf -v quoted '%q' "$value"
  awk -v key="$key" -v line="$key=$quoted" '
    BEGIN { done = 0 }
    $0 ~ "^[[:space:]]*" key "=" { if (!done) { print line; done = 1 } ; next }
    { print }
    END { if (!done) print line }
  ' "$env_file" > "$tmp"
  mv "$tmp" "$env_file"
  chmod 600 "$env_file" 2>/dev/null || true
}
p25_deploy_require_password() {
  local prompted=0
  : "${PI_USER:=pi}"
  : "${PI_HOST:=PI-SDR}"
  : "${PI_REPO:=/home/pi/PI-P25-SCANNER}"
  if [[ -n "$PI_PASSWORD_ARG" ]]; then
    PI_PASSWORD="$PI_PASSWORD_ARG"
  elif [[ -n "${PI_PASSWORD:-}" ]]; then
    PI_PASSWORD="$PI_PASSWORD"
  elif [[ -n "${SSHPASS:-}" ]]; then
    PI_PASSWORD="$SSHPASS"
  else
    read -r -s -p "Pi password for ${PI_USER}@${PI_HOST}: " PI_PASSWORD
    echo
    prompted=1
  fi
  if [[ -z "${PI_PASSWORD:-}" ]]; then
    fail "empty Pi password"
    finish
  fi
  export PI_USER PI_HOST PI_REPO PI_PASSWORD
  if [[ "$prompted" -eq 1 || -n "$PI_PASSWORD_ARG" || ! -f "$(p25_deploy_env_file)" ]]; then
    p25_deploy_dotenv_set PI_USER "$PI_USER"
    p25_deploy_dotenv_set PI_HOST "$PI_HOST"
    p25_deploy_dotenv_set PI_REPO "$PI_REPO"
    p25_deploy_dotenv_set PI_PASSWORD "$PI_PASSWORD"
    pass "saved Pi SSH settings to $(p25_deploy_env_file)"
    warn "$(p25_deploy_env_file) is local plaintext; keep it ignored and do not upload it"
  fi
}

p25_deploy_load_dotenv
if [[ -n "$PI_HOST_ARG" ]]; then export PI_HOST="$PI_HOST_ARG"; fi
if [[ -n "$PI_USER_ARG" ]]; then export PI_USER="$PI_USER_ARG"; fi
if [[ -n "$PI_REPO_ARG" ]]; then export PI_REPO="$PI_REPO_ARG"; fi
: "${PI_USER:=pi}"
: "${PI_HOST:=PI-SDR}"
: "${PI_REPO:=/home/pi/PI-P25-SCANNER}"
p25_deploy_require_password
pass "Pi connection settings loaded for ${PI_USER}@${PI_HOST}:${PI_REPO}"

for cmd in sshpass ssh scp tar python3 base64; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "command available: $cmd"
  else
    fail "missing required command: $cmd"
  fi
done
if [[ "$FAIL_COUNT" -ne 0 ]]; then
  finish
fi

SSH_OPTS=(
  -o StrictHostKeyChecking=accept-new
  -o UserKnownHostsFile="$HOME/.ssh/known_hosts"
  -o PreferredAuthentications=password,keyboard-interactive,publickey
  -o PubkeyAuthentication=yes
  -o BatchMode=no
  -o ConnectTimeout=15
)
SSH=(sshpass -p "$PI_PASSWORD" ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}")
SCP=(sshpass -p "$PI_PASSWORD" scp -O "${SSH_OPTS[@]}")

required_files=(
  src/pi_p25_scanner/backend_launch.py
  tools/pi5_p25_browser_audio_raw_bridge_server.py
  tools/pi5_p25_install_raw_audio_service_v0_3s.sh
)
for path in "${required_files[@]}"; do
  if [[ -f "$path" ]]; then
    pass "deploy file exists: $path"
  else
    fail "missing deploy file: $path"
  fi
done
if [[ "$FAIL_COUNT" -ne 0 ]]; then
  finish
fi

if python3 -m py_compile tools/pi5_p25_browser_audio_raw_bridge_server.py >>"$REPORT_FILE" 2>&1; then
  pass "raw bridge python compile passed"
else
  fail "raw bridge python compile failed"
fi
if bash -n tools/pi5_p25_install_raw_audio_service_v0_3s.sh >>"$REPORT_FILE" 2>&1; then
  pass "Pi installer shell syntax passed"
else
  fail "Pi installer shell syntax failed"
fi
if [[ "$FAIL_COUNT" -ne 0 ]]; then
  finish
fi

tar -czf "$TMP_TARBALL" "${required_files[@]}"
pass "created deploy tarball: $TMP_TARBALL"

REMOTE_PASSWORD_B64="$(printf '%s' "$PI_PASSWORD" | base64 | tr -d '\n')"
cat > "$REMOTE_SCRIPT_LOCAL" <<REMOTE
#!/usr/bin/env bash
set -Eeuo pipefail
REPO='$PI_REPO'
TARBALL='$REMOTE_TARBALL'
NO_RESTART_BACKEND='$NO_RESTART_BACKEND'
export SUDO_PASSWORD="\$(printf '%s' '$REMOTE_PASSWORD_B64' | base64 -d)"
printf 'Remote deploy repo: %s\n' "\$REPO"
if [[ ! -d "\$REPO" ]]; then
  printf 'FAIL: repo directory missing: %s\n' "\$REPO" >&2
  exit 10
fi
cd "\$REPO"
tar -xzf "\$TARBALL"
chmod +x tools/pi5_p25_install_raw_audio_service_v0_3s.sh tools/pi5_p25_browser_audio_raw_bridge_server.py
./tools/pi5_p25_install_raw_audio_service_v0_3s.sh --install --yes
if [[ "\$NO_RESTART_BACKEND" -ne 1 ]]; then
  if systemctl list-unit-files pi-p25-scanner.service >/dev/null 2>&1; then
    printf '%s\n' "\$SUDO_PASSWORD" | sudo -S systemctl restart pi-p25-scanner.service
    printf 'PASS: restarted pi-p25-scanner.service\n'
  else
    printf 'WARN: pi-p25-scanner.service unit not found; backend not restarted\n'
  fi
fi
python3 - <<'PY'
import json
import urllib.request
for url in ('http://127.0.0.1:8070/api/status', 'http://127.0.0.1:8072/api/audio/status'):
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = resp.read(2000).decode('utf-8', errors='replace')
        print('PROBE_OK', url, body[:300].replace('\n', ' '))
    except Exception as exc:
        print('PROBE_WARN', url, type(exc).__name__, exc)
PY
REMOTE
chmod +x "$REMOTE_SCRIPT_LOCAL"
pass "created remote install script: $REMOTE_SCRIPT_LOCAL"

"${SCP[@]}" "$TMP_TARBALL" "${PI_USER}@${PI_HOST}:${REMOTE_TARBALL}" >>"$REPORT_FILE" 2>&1
pass "copied deploy tarball to ${PI_USER}@${PI_HOST}:${REMOTE_TARBALL}"
"${SCP[@]}" "$REMOTE_SCRIPT_LOCAL" "${PI_USER}@${PI_HOST}:${REMOTE_SCRIPT}" >>"$REPORT_FILE" 2>&1
pass "copied remote install script to ${PI_USER}@${PI_HOST}:${REMOTE_SCRIPT}"

"${SSH[@]}" "bash '$REMOTE_SCRIPT'" 2>&1 | tee -a "$REPORT_FILE"
pass "remote raw audio service deploy completed"

"${SSH[@]}" "rm -f '$REMOTE_TARBALL' '$REMOTE_SCRIPT'" >>"$REPORT_FILE" 2>&1 || warn "remote cleanup failed"
rm -f "$TMP_TARBALL" "$REMOTE_SCRIPT_LOCAL" || true
pass "local temporary files cleaned"

printf '\nNext test:\n' | tee -a "$REPORT_FILE"
printf '  Open http://%s:8070\n' "$PI_HOST" | tee -a "$REPORT_FILE"
printf '  Start Scanner, then open http://%s:8072/audio.wav\n' "$PI_HOST" | tee -a "$REPORT_FILE"
finish
