#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/msys2_env_common.sh
. "$SCRIPT_DIR/msys2_env_common.sh"
p25_load_dotenv

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-PI-SDR}"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
LOCAL_DOWNLOAD_DIR="${LOCAL_DOWNLOAD_DIR:-/c/Users/jim/Downloads/pi-p25-command-logs}"
LABEL="http_runtime_probe"
SECONDS_TO_RUN=30
INTERVAL=1
PI_PASSWORD_ARG=""
SELF_TEST=0

usage() {
  cat <<'EOF_USAGE'
Usage:
  ./tools/msys2_run_pi_http_runtime_probe_and_pull_log.sh [options]

Runs the HTTP runtime probe on the Pi over sshpass, then pulls the generated
upload-ready log into /c/Users/jim/Downloads/pi-p25-command-logs.

Password handling:
  Loads .env first. If PI_PASSWORD is missing, prompts once and saves it to .env.

Defaults:
  Pi user: pi
  Pi host: PI-SDR
  Pi repo: /home/pi/n0jcg-scanner
  Windows/MSYS2 downloads: /c/Users/jim/Downloads/pi-p25-command-logs

Options:
  --host HOST       Pi host name or pi@HOST. Default: PI-SDR
  --user USER       Pi SSH user. Default: pi
  --repo PATH       Pi repo path. Default: /home/pi/n0jcg-scanner
  --seconds N       Probe duration. Default: 30
  --interval N      Probe interval. Default: 1
  --dest PATH       Local MSYS2 destination directory
  --password PASS   Pi password for sshpass. Prefer .env/PI_PASSWORD instead.
  --self-test       Validate local defaults and exit without SSH
  -h, --help        Show this help
EOF_USAGE
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --host)
      shift
      PI_HOST="$1"
      shift
      ;;
    --user)
      shift
      PI_USER="$1"
      shift
      ;;
    --repo)
      shift
      PI_REPO="$1"
      shift
      ;;
    --seconds)
      shift
      SECONDS_TO_RUN="$1"
      shift
      ;;
    --interval)
      shift
      INTERVAL="$1"
      shift
      ;;
    --dest)
      shift
      LOCAL_DOWNLOAD_DIR="$1"
      shift
      ;;
    --password)
      shift
      PI_PASSWORD_ARG="$1"
      shift
      ;;
    --self-test)
      SELF_TEST=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "FAIL: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$PI_HOST" == *@* ]]; then
  PI_USER="${PI_HOST%@*}"
  PI_HOST="${PI_HOST#*@}"
fi

if [[ "$SELF_TEST" -eq 1 ]]; then
  echo "PASS: self-test mode"
  echo "PI_USER=$PI_USER"
  echo "PI_HOST=$PI_HOST"
  echo "PI_REPO=$PI_REPO"
  echo "LOCAL_DOWNLOAD_DIR=$LOCAL_DOWNLOAD_DIR"
  [[ "$PI_USER" == "pi" ]]
  [[ "$PI_REPO" == "/home/pi/n0jcg-scanner" ]]
  [[ "$LOCAL_DOWNLOAD_DIR" == /c/Users/jim/Downloads/* ]]
  echo "FINAL: PASS"
  exit 0
fi

if ! [[ "$SECONDS_TO_RUN" =~ ^[0-9]+$ ]] || [[ "$SECONDS_TO_RUN" -le 0 ]]; then
  echo "FAIL: --seconds must be a positive integer" >&2
  exit 2
fi
if ! [[ "$INTERVAL" =~ ^[0-9]+$ ]] || [[ "$INTERVAL" -le 0 ]]; then
  echo "FAIL: --interval must be a positive integer" >&2
  exit 2
fi
if ! command -v sshpass >/dev/null 2>&1; then
  echo "FAIL: sshpass is required in MSYS2" >&2
  exit 1
fi
if [[ ! -x ./tools/msys2_pull_latest_p25_log.sh ]]; then
  echo "FAIL: ./tools/msys2_pull_latest_p25_log.sh not found or not executable" >&2
  exit 1
fi

p25_require_pi_password "$PI_PASSWORD_ARG" "$PI_USER" "$PI_HOST"

REMOTE_SCRIPT='set -u
cd "$1" || exit 10
git pull
printf "%s\n" "$2" | sudo -S -p "" systemctl restart pi-p25-scanner.service
./tools/pi5_p25_run_with_log.sh --label http_runtime_probe -- ./tools/pi5_p25_op25_http_runtime_probe.sh --seconds "$3" --interval "$4" --yes
'

set +e
sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=accept-new "${PI_USER}@${PI_HOST}" \
  "bash -s -- '$PI_REPO' '$PI_PASSWORD' '$SECONDS_TO_RUN' '$INTERVAL'" <<< "$REMOTE_SCRIPT"
REMOTE_RC=$?
set -e

echo "Remote probe exit status: $REMOTE_RC"

echo "Pulling latest Pi log into Windows Downloads..."
./tools/msys2_pull_latest_p25_log.sh \
  --user "$PI_USER" \
  --host "$PI_HOST" \
  --repo "$PI_REPO" \
  --label "$LABEL" \
  --dest "$LOCAL_DOWNLOAD_DIR" \
  --password "$PI_PASSWORD"

exit "$REMOTE_RC"
