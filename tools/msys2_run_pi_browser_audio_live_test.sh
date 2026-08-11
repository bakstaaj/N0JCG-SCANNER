#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-PI-SDR}"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
LOCAL_DOWNLOAD_DIR="${LOCAL_DOWNLOAD_DIR:-/c/Users/jim/Downloads/pi-p25-command-logs}"
SECONDS_TO_RUN=120
HTTP_PORT=8072
UDP_PORT=23456
PREBUFFER_CHUNKS=0
DECLICK_SAMPLES=0
FLAG_DROP_HOLD_MS=2500
ENCRYPTED_LOG_HOLD_MS=5000
OP25_VERBOSITY=10
DISABLE_LOG_GATE=0
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

Runs the V0.3K encrypted-log-gated browser-audio listening test on the Pi and
pulls the log back to /c/Users/jim/Downloads/pi-p25-command-logs after it finishes.

Options:
  --host HOST                 Pi host name or pi@HOST. Default: PI-SDR
  --user USER                 Pi SSH user. Default: pi
  --repo PATH                 Pi repo path. Default: /home/pi/n0jcg-scanner
  --seconds N                 Test duration. Default: 120
  --http-port N               Browser audio HTTP port. Default: 8072
  --udp-port N                OP25 UDP PCM port. Default: 23456
  --flag-drop-hold-ms N       Hold after OP25 2-byte audio flags. Default: 2500
  --encrypted-log-hold-ms N   Hold after OP25 encrypted log indicators. Default: 5000
  --op25-verbosity N          OP25 -v value. Default: 10
  --disable-log-gate          Disable OP25 encrypted-log watcher
  --prebuffer-chunks N        Accepted for compatibility; ignored
  --declick-samples N         Accepted for compatibility; ignored
  --dest PATH                 Local MSYS2 destination directory
  --password PASS             Pi password for sshpass. Prefer PI_PASSWORD in .env.
  -h, --help                  Show this help

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
    --flag-drop-hold-ms) shift; FLAG_DROP_HOLD_MS="$1"; shift ;;
    --encrypted-log-hold-ms) shift; ENCRYPTED_LOG_HOLD_MS="$1"; shift ;;
    --op25-verbosity) shift; OP25_VERBOSITY="$1"; shift ;;
    --disable-log-gate) DISABLE_LOG_GATE=1; shift ;;
    --prebuffer-chunks) shift; PREBUFFER_CHUNKS="$1"; shift ;;
    --declick-samples) shift; DECLICK_SAMPLES="$1"; shift ;;
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
extra=()
if [[ "$10" == "1" ]]; then
  extra+=(--disable-log-gate)
fi
./tools/pi5_p25_run_with_log.sh --label browser_audio_live_test -- ./tools/pi5_p25_op25_browser_audio_live_test.sh --seconds "$2" --http-port "$3" --udp-port "$4" --prebuffer-chunks "$5" --declick-samples "$6" --flag-drop-hold-ms "$7" --encrypted-log-hold-ms "$8" --op25-verbosity "$9" "${extra[@]}" --yes
'

set +e
sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=accept-new "${PI_USER}@${PI_HOST}" \
  "bash -s -- '$PI_REPO' '$SECONDS_TO_RUN' '$HTTP_PORT' '$UDP_PORT' '$PREBUFFER_CHUNKS' '$DECLICK_SAMPLES' '$FLAG_DROP_HOLD_MS' '$ENCRYPTED_LOG_HOLD_MS' '$OP25_VERBOSITY' '$DISABLE_LOG_GATE'" <<< "$REMOTE_SCRIPT"
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
