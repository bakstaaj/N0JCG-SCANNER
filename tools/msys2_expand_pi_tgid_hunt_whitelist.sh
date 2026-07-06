#!/usr/bin/env bash
# Run the TGID hunt whitelist expansion on the Pi from MSYS2 and pull the log.
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-PI-SDR}"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
LOCAL_DOWNLOAD_DIR="${LOCAL_DOWNLOAD_DIR:-/c/Users/jim/Downloads/pi-p25-command-logs}"
START_TGID=2500
END_TGID=4500
BLACKLIST_KNOWN=1
USE_LOG=1
PI_PASSWORD_ARG=""

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a
  . ./.env
  set +a
fi
if [[ -f ./tools/msys2_env_common.sh ]]; then
  # shellcheck disable=SC1091
  . ./tools/msys2_env_common.sh >/dev/null 2>&1 || true
fi

usage() {
  cat <<'USAGE'
Usage:
  ./tools/msys2_expand_pi_tgid_hunt_whitelist.sh [options]

Expands the active Pi OP25 runtime whitelist for TOPAZ/TRWC clear-traffic hunting.
Default range is 2500-4500 and known encrypted TGIDs are blacklisted.

Options:
  --host HOST                  Pi host name or pi@HOST. Default: PI-SDR
  --user USER                  Pi SSH user. Default: pi
  --repo PATH                  Pi repo path. Default: /home/pi/PI-P25-SCANNER
  --start N                    First TGID to include. Default: 2500
  --end N                      Last TGID to include. Default: 4500
  --include-known-encrypted    Do not blacklist known encrypted TGIDs
  --blacklist-known-encrypted  Blacklist known encrypted TGIDs. Default
  --no-log                     Do not inspect recent OP25 logs for encrypted TGIDs
  --dest PATH                  Local MSYS2 destination directory
  --password PASS              Pi password for sshpass. Prefer PI_PASSWORD in .env.
  -h, --help                   Show this help

After this passes, run a short browser audio test:
  ./tools/msys2_run_pi_browser_audio_live_test.sh --seconds 120 --op25-verbosity 10 --flag-drop-hold-ms 2500 --encrypted-log-hold-ms 5000
USAGE
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --host) shift; PI_HOST="$1"; shift ;;
    --user) shift; PI_USER="$1"; shift ;;
    --repo) shift; PI_REPO="$1"; shift ;;
    --start) shift; START_TGID="$1"; shift ;;
    --end) shift; END_TGID="$1"; shift ;;
    --include-known-encrypted) BLACKLIST_KNOWN=0; shift ;;
    --blacklist-known-encrypted) BLACKLIST_KNOWN=1; shift ;;
    --no-log) USE_LOG=0; shift ;;
    --dest) shift; LOCAL_DOWNLOAD_DIR="$1"; shift ;;
    --password) shift; PI_PASSWORD_ARG="$1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "FAIL: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$PI_HOST" == *@* ]]; then
  PI_USER="${PI_HOST%@*}"
  PI_HOST="${PI_HOST#*@}"
fi
if ! command -v sshpass >/dev/null 2>&1; then
  echo "FAIL: sshpass is required in MSYS2" >&2
  exit 1
fi
if [[ -n "$PI_PASSWORD_ARG" ]]; then
  PI_PASSWORD="$PI_PASSWORD_ARG"
elif [[ -n "${PI_PASSWORD:-}" ]]; then
  PI_PASSWORD="$PI_PASSWORD"
elif [[ -n "${SSHPASS:-}" ]]; then
  PI_PASSWORD="$SSHPASS"
else
  read -r -s -p "Pi password for ${PI_USER}@${PI_HOST}: " PI_PASSWORD
  echo
fi
if [[ -z "$PI_PASSWORD" ]]; then
  echo "FAIL: empty Pi password" >&2
  exit 1
fi

REMOTE_SCRIPT='set -euo pipefail
cd "$1"
git pull
args=(--apply --yes --start "$2" --end "$3")
if [[ "$4" == "1" ]]; then
  args+=(--blacklist-known-encrypted)
else
  args+=(--include-known-encrypted)
fi
if [[ "$5" == "1" ]]; then
  args+=(--from-log)
else
  args+=(--no-log)
fi
./tools/pi5_p25_run_with_log.sh --label tgid_hunt_expand -- ./tools/pi5_p25_expand_op25_tgid_hunt_whitelist.sh "${args[@]}"
'

set +e
sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=accept-new "${PI_USER}@${PI_HOST}" \
  "bash -s -- '$PI_REPO' '$START_TGID' '$END_TGID' '$BLACKLIST_KNOWN' '$USE_LOG'" <<< "$REMOTE_SCRIPT"
REMOTE_RC=$?
set -e

echo "Remote TGID hunt expansion exit status: $REMOTE_RC"

if [[ -x ./tools/msys2_pull_latest_p25_log.sh ]]; then
  ./tools/msys2_pull_latest_p25_log.sh \
    --user "$PI_USER" \
    --host "$PI_HOST" \
    --repo "$PI_REPO" \
    --label "tgid_hunt_expand" \
    --dest "$LOCAL_DOWNLOAD_DIR" \
    --password "$PI_PASSWORD" || true
else
  echo "WARN: local log pull helper not found; skipping log pull"
fi

exit "$REMOTE_RC"
