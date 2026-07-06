#!/usr/bin/env bash
set -Eeuo pipefail

HOST="${P25_PI_HOST:-pi@PI-SDR}"
REMOTE_REPO="${P25_PI_REPO:-~/PI-P25-SCANNER}"
LABEL=""
DEST=""
STRICT_LABEL=0

usage() {
  cat <<'EOF_USAGE'
Usage:
  ./tools/msys2_pull_latest_p25_log.sh [--host USER@HOST] [--remote-repo PATH] [--label NAME] [--dest DIR]

Examples:
  ./tools/msys2_pull_latest_p25_log.sh
  ./tools/msys2_pull_latest_p25_log.sh --host pi@PI-SDR
  ./tools/msys2_pull_latest_p25_log.sh --host pi@192.168.1.50 --label http_runtime_probe

This helper copies the latest Pi-side .p25_command_logs/*.txt file into a Windows/MSYS2 local upload folder.
Default destination is /c/Users/$USER/Downloads/pi-p25-command-logs when that folder exists.
EOF_USAGE
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --host)
      shift
      HOST="${1:-}"
      shift || true
      ;;
    --remote-repo)
      shift
      REMOTE_REPO="${1:-}"
      shift || true
      ;;
    --label)
      shift
      LABEL="${1:-}"
      STRICT_LABEL=1
      shift || true
      ;;
    --dest)
      shift
      DEST="${1:-}"
      shift || true
      ;;
    --help|-h)
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

if [[ -z "$HOST" ]]; then
  echo "FAIL: host is empty" >&2
  exit 2
fi
if [[ -z "$REMOTE_REPO" ]]; then
  echo "FAIL: remote repo path is empty" >&2
  exit 2
fi

if [[ -z "$DEST" ]]; then
  if [[ -n "${USER:-}" && -d "/c/Users/${USER}/Downloads" ]]; then
    DEST="/c/Users/${USER}/Downloads/pi-p25-command-logs"
  elif [[ -n "${USERNAME:-}" && -d "/c/Users/${USERNAME}/Downloads" ]]; then
    DEST="/c/Users/${USERNAME}/Downloads/pi-p25-command-logs"
  else
    DEST="$HOME/Downloads/pi-p25-command-logs"
  fi
fi

if ! command -v ssh >/dev/null 2>&1; then
  echo "FAIL: ssh is required in MSYS2" >&2
  exit 1
fi
if ! command -v scp >/dev/null 2>&1; then
  echo "FAIL: scp is required in MSYS2" >&2
  exit 1
fi
mkdir -p "$DEST"

SAFE_LABEL=""
if [[ -n "$LABEL" ]]; then
  SAFE_LABEL="$(printf '%s' "$LABEL" | tr -c 'A-Za-z0-9_.-' '_' | sed 's/^_*//; s/_*$//')"
  if [[ -z "$SAFE_LABEL" ]]; then
    echo "FAIL: --label did not contain any safe filename characters" >&2
    exit 2
  fi
fi

REMOTE_SCRIPT='set -e
cd "$1"
if [ ! -d .p25_command_logs ]; then
  echo "__NO_LOG_DIR__"
  exit 0
fi
if [ -n "$2" ]; then
  latest=$(ls -1t ".p25_command_logs/${2}"*.txt 2>/dev/null | head -n 1 || true)
else
  latest=$(ls -1t .p25_command_logs/*.txt 2>/dev/null | head -n 1 || true)
fi
if [ -z "$latest" ]; then
  echo "__NO_LOG_FILE__"
else
  printf "%s\n" "$latest"
fi'

LATEST="$(ssh "$HOST" bash -s -- "$REMOTE_REPO" "$SAFE_LABEL" <<< "$REMOTE_SCRIPT")"
if [[ "$LATEST" == "__NO_LOG_DIR__" ]]; then
  echo "FAIL: remote log directory does not exist: ${REMOTE_REPO}/.p25_command_logs" >&2
  exit 1
fi
if [[ "$LATEST" == "__NO_LOG_FILE__" || -z "$LATEST" ]]; then
  if [[ "$STRICT_LABEL" -eq 1 ]]; then
    echo "FAIL: no remote log file found for label prefix: $SAFE_LABEL" >&2
  else
    echo "FAIL: no remote log files found under ${REMOTE_REPO}/.p25_command_logs" >&2
  fi
  exit 1
fi

BASENAME="$(basename "$LATEST")"
LOCAL_FILE="$DEST/$BASENAME"
REMOTE_SPEC="$HOST:$REMOTE_REPO/$LATEST"

if scp -O "$REMOTE_SPEC" "$LOCAL_FILE" >/dev/null 2>&1; then
  :
else
  scp "$REMOTE_SPEC" "$LOCAL_FILE"
fi

if [[ ! -s "$LOCAL_FILE" ]]; then
  echo "FAIL: local copied file is missing or empty: $LOCAL_FILE" >&2
  exit 1
fi

echo "PASS: copied latest Pi command log"
echo "Remote log file: $REMOTE_REPO/$LATEST"
echo "Local log file: $LOCAL_FILE"
if command -v cygpath >/dev/null 2>&1; then
  echo "Windows path: $(cygpath -w "$LOCAL_FILE")"
fi
echo "FINAL: PASS"
