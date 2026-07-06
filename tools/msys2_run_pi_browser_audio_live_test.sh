#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-PI-SDR}"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
LOCAL_DOWNLOAD_DIR="${LOCAL_DOWNLOAD_DIR:-/c/Users/jim/Downloads/pi-p25-command-logs}"
SECONDS_TO_RUN=600
HTTP_PORT=8072
UDP_PORT=23456
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
  cat <<'EOF_USAGE'
Usage:
  ./tools/msys2_run_pi_browser_audio_live_test.sh [options]

Runs the V0.3D browser-audio live listening test on the Pi and pulls the log
back to /c/Users/jim/Downloads/pi-p25-command-logs after it finishes.

Options:
  --host HOST       Pi host name or pi@HOST. Default: PI-SDR
  --user USER       Pi SSH user. Default: pi
  --repo PATH       Pi repo path. Default: /home/pi/PI-P25-SCANNER
  --seconds N       Test duration. Default: 600
  --http-port N     Browser audio HTTP port. Default: 8072
  --udp-port N      OP25 UDP PCM port. Default: 23456
  --dest PATH       Local MSYS2 destination directory
  --password PASS   Pi password for sshpass. Prefer PI_PASSWORD in .env.
  -h, --help        Show this help

During the test, open the printed BROWSER_AUDIO_URL in your browser.
EOF_USAGE
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --host) shift; PI_HOST="$1"; shift ;;
    --user) shift; PI_USER="$1"; shift ;;
    --repo) shift; PI_REPO="$1"; shift ;;
    --seconds) shift; SECONDS_TO_RUN="$1"; shift ;;
    --http-port) shift; HTTP_PORT="$1"; shift ;;
    --udp-port) shift; UDP_PORT="$1"; shift ;;
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
./tools/pi5_p25_run_with_log.sh --label browser_audio_live_test -- ./tools/pi5_p25_op25_browser_audio_live_test.sh --seconds "$2" --http-port "$3" --udp-port "$4" --yes
'

set +e
sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=accept-new "${PI_USER}@${PI_HOST}" \
  "bash -s -- '$PI_REPO' '$SECONDS_TO_RUN' '$HTTP_PORT' '$UDP_PORT'" <<< "$REMOTE_SCRIPT"
REMOTE_RC=$?
set -e

echo "Remote browser-audio live-test exit status: $REMOTE_RC"

if [[ -x ./tools/msys2_pull_latest_p25_log.sh ]]; then
  ./tools/msys2_pull_latest_p25_log.sh \
    --user "$PI_USER" \
    --host "$PI_HOST" \
    --repo "$PI_REPO" \
    --label "browser_audio_live_test" \
    --dest "$LOCAL_DOWNLOAD_DIR" \
    --password "$PI_PASSWORD" || true
else
  echo "WARN: local log pull helper not found; skipping log pull"
fi

exit "$REMOTE_RC"
