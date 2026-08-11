#!/usr/bin/env bash
# Upload/apply a scanner JSON config template from MSYS2 to the Raspberry Pi.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
REPORT_FILE="$LOG_DIR/upload_config_template_${STAMP}.txt"
TEMPLATE="config/templates/topaz_trwc_mesa_discovery_2500_4500.json"
PI_HOST_ARG=""
PI_USER_ARG=""
PI_REPO_ARG=""
RESTART_BACKEND=0

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
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "upload/apply aborted unexpectedly at line $LINENO rc=$rc"; finish; fi' ERR

usage() {
  cat <<USAGE
Usage:
  ./tools/msys2_upload_pi_config_template.sh [--template config/templates/topaz_trwc_mesa_discovery_2500_4500.json] [--host PI-SDR] [--user pi] [--repo /home/pi/n0jcg-scanner] [--restart-backend]

Uploads the selected JSON config template to the Pi, applies it as runtime/settings/p25_systems.json, backs up the previous runtime config, and regenerates OP25 runtime files.
USAGE
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --template) shift; TEMPLATE="$1"; shift ;;
    --host) shift; PI_HOST_ARG="$1"; shift ;;
    --user) shift; PI_USER_ARG="$1"; shift ;;
    --repo) shift; PI_REPO_ARG="$1"; shift ;;
    --restart-backend) RESTART_BACKEND=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

printf '=== scanner upload/apply config template ===\n' | tee -a "$REPORT_FILE"
printf 'Started UTC: %s\n' "$STAMP" | tee -a "$REPORT_FILE"
printf 'Working directory: %s\n' "$(pwd)" | tee -a "$REPORT_FILE"

if [[ -f DEV_GUARDRAILS.md && -d src/pi_p25_scanner && -d tools ]]; then pass "running from scanner repository root"; else fail "run from scanner repository root"; finish; fi
case "$(uname -s 2>/dev/null || true)" in MINGW*|MSYS*) pass "MSYS2 shell detected" ;; *) warn "shell does not look like MSYS2" ;; esac

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
  pass "loaded .env"
else
  warn ".env not found; PI_PASSWORD or SSHPASS must be exported or you will be prompted"
fi
PI_HOST="${PI_HOST_ARG:-${PI_HOST:-PI-SDR}}"
PI_USER="${PI_USER_ARG:-${PI_USER:-pi}}"
PI_REPO="${PI_REPO_ARG:-${PI_REPO:-/home/pi/n0jcg-scanner}}"
if [[ -n "${PI_PASSWORD:-}" ]]; then :; elif [[ -n "${SSHPASS:-}" ]]; then PI_PASSWORD="$SSHPASS"; else read -r -s -p "Pi password for ${PI_USER}@${PI_HOST}: " PI_PASSWORD; echo; fi
if [[ -z "${PI_PASSWORD:-}" ]]; then fail "empty Pi password"; finish; fi
export PI_PASSWORD
pass "Pi connection settings loaded for ${PI_USER}@${PI_HOST}:${PI_REPO}"

for cmd in sshpass ssh scp python3; do command -v "$cmd" >/dev/null 2>&1 && pass "command available: $cmd" || fail "missing required command: $cmd"; done
[[ -f "$TEMPLATE" ]] && pass "template exists: $TEMPLATE" || fail "template missing: $TEMPLATE"
[[ -f tools/pi5_p25_apply_config_template.sh ]] && pass "Pi apply helper exists" || fail "missing tools/pi5_p25_apply_config_template.sh"
if [[ "$FAIL_COUNT" -ne 0 ]]; then finish; fi

python3 -m json.tool "$TEMPLATE" >/dev/null
pass "template JSON syntax passed"

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -o PreferredAuthentications=password,keyboard-interactive,publickey)
SSH=(sshpass -p "$PI_PASSWORD" ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}")
SCP=(sshpass -p "$PI_PASSWORD" scp -O "${SSH_OPTS[@]}")
REMOTE_TEMPLATE="/tmp/pi_p25_template_${STAMP}.json"
REMOTE_HELPER="/tmp/pi5_p25_apply_config_template_${STAMP}.sh"
REMOTE_PASSWORD_B64="$(printf '%s' "$PI_PASSWORD" | base64 | tr -d '\n')"

"${SCP[@]}" "$TEMPLATE" "${PI_USER}@${PI_HOST}:${REMOTE_TEMPLATE}" >>"$REPORT_FILE" 2>&1
pass "copied template to ${PI_USER}@${PI_HOST}:${REMOTE_TEMPLATE}"
"${SCP[@]}" tools/pi5_p25_apply_config_template.sh "${PI_USER}@${PI_HOST}:${REMOTE_HELPER}" >>"$REPORT_FILE" 2>&1
pass "copied Pi apply helper to ${PI_USER}@${PI_HOST}:${REMOTE_HELPER}"

REMOTE_CMD="chmod +x '$REMOTE_HELPER'; export SUDO_PASSWORD=\$(printf '%s' '$REMOTE_PASSWORD_B64' | base64 -d); bash '$REMOTE_HELPER' --template '$REMOTE_TEMPLATE' --repo '$PI_REPO' --yes"
if [[ "$RESTART_BACKEND" -eq 1 ]]; then REMOTE_CMD+=" --restart-backend"; fi
"${SSH[@]}" "$REMOTE_CMD" 2>&1 | tee -a "$REPORT_FILE"
pass "remote template apply completed"

"${SSH[@]}" "rm -f '$REMOTE_TEMPLATE' '$REMOTE_HELPER'" >>"$REPORT_FILE" 2>&1 || warn "remote cleanup failed"
pass "remote temporary files cleaned"

printf '\nNext steps:\n' | tee -a "$REPORT_FILE"
printf '  1. Open http://%s:8070\n' "$PI_HOST" | tee -a "$REPORT_FILE"
printf '  2. Stop Scanner if it is running\n' | tee -a "$REPORT_FILE"
printf '  3. Tap Start Scanner + Audio\n' | tee -a "$REPORT_FILE"
finish
