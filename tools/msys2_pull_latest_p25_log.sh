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
REMOTE_FILE=""
PI_PASSWORD_ARG=""
SELF_TEST=0

usage() {
  cat <<'EOF_USAGE'
Usage:
  ./tools/msys2_pull_latest_p25_log.sh [options]

Defaults are intentionally matched to Jim's workflow:
  Pi user: pi
  Pi host: PI-SDR
  Pi repo: /home/pi/n0jcg-scanner
  Windows/MSYS2 downloads: /c/Users/jim/Downloads/pi-p25-command-logs

Options:
  --host HOST             Pi host name or pi@HOST. Default: PI-SDR
  --user USER             Pi SSH user. Default: pi
  --repo PATH             Pi repo path. Default: /home/pi/n0jcg-scanner
  --label NAME            Log label prefix. Default: http_runtime_probe
  --remote-file PATH      Pull this exact Pi-side file instead of latest by label
  --dest PATH             Local MSYS2 destination directory. Default: /c/Users/jim/Downloads/pi-p25-command-logs
  --password PASS         Pi password for sshpass. Prefer .env/PI_PASSWORD instead.
  --self-test             Validate local defaults and exit without SSH
  -h, --help              Show this help

Password handling:
  Loads .env first. Uses PI_PASSWORD, then SSHPASS, then prompts once and saves PI_PASSWORD to .env.

Output:
  Prints UPLOAD_FILE_MSYS and UPLOAD_FILE_WINDOWS for the copied file.
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
    --label)
      shift
      LABEL="$1"
      shift
      ;;
    --remote-file)
      shift
      REMOTE_FILE="$1"
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
  if [[ "$PI_USER" != "pi" ]]; then
    echo "FAIL: default Pi user should be pi" >&2
    exit 1
  fi
  if [[ "$LOCAL_DOWNLOAD_DIR" != /c/Users/jim/Downloads/* ]]; then
    echo "FAIL: default local path should be under /c/Users/jim/Downloads" >&2
    exit 1
  fi
  echo "FINAL: PASS"
  exit 0
fi

if ! command -v sshpass >/dev/null 2>&1; then
  echo "FAIL: sshpass is required in MSYS2. Install it before pulling Pi logs." >&2
  exit 1
fi

if ! command -v scp >/dev/null 2>&1; then
  echo "FAIL: scp is required in MSYS2" >&2
  exit 1
fi

p25_require_pi_password "$PI_PASSWORD_ARG" "$PI_USER" "$PI_HOST"

mkdir -p "$LOCAL_DOWNLOAD_DIR"

sq() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

if [[ -n "$REMOTE_FILE" ]]; then
  REMOTE_SELECTED="$REMOTE_FILE"
else
  REMOTE_LOG_DIR="$PI_REPO/.p25_command_logs"
  REMOTE_CMD="find $(sq "$REMOTE_LOG_DIR") -maxdepth 1 -type f -name $(sq "${LABEL}_*.txt") -printf '%T@ %p\\n' | sort -nr | head -n 1 | cut -d' ' -f2-"
  set +e
  REMOTE_SELECTED="$(sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=accept-new "${PI_USER}@${PI_HOST}" "bash -lc $(sq "$REMOTE_CMD")")"
  SSH_RC=$?
  set -e
  if [[ "$SSH_RC" -ne 0 ]]; then
    echo "FAIL: ssh command failed while locating latest Pi log" >&2
    exit "$SSH_RC"
  fi
fi

if [[ -z "$REMOTE_SELECTED" ]]; then
  echo "FAIL: no Pi log found for label '${LABEL}' under ${PI_REPO}/.p25_command_logs" >&2
  exit 1
fi

BASE_NAME="$(basename "$REMOTE_SELECTED")"
LOCAL_FILE="$LOCAL_DOWNLOAD_DIR/$BASE_NAME"

sshpass -p "$PI_PASSWORD" scp -O -o StrictHostKeyChecking=accept-new "${PI_USER}@${PI_HOST}:$REMOTE_SELECTED" "$LOCAL_FILE"

if [[ ! -f "$LOCAL_FILE" ]]; then
  echo "FAIL: local copied file was not created: $LOCAL_FILE" >&2
  exit 1
fi

echo "PASS: copied Pi log"
echo "PI_REMOTE_FILE=$REMOTE_SELECTED"
echo "UPLOAD_FILE_MSYS=$LOCAL_FILE"
if command -v cygpath >/dev/null 2>&1; then
  echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOCAL_FILE")"
else
  echo "UPLOAD_FILE_WINDOWS=$LOCAL_FILE"
fi
echo "FINAL: PASS"
