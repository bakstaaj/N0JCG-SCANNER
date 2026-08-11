#!/usr/bin/env bash
set -u

LABEL="command"
LOG_DIR="${P25_COMMAND_LOG_DIR:-.p25_command_logs}"

usage() {
  cat <<'EOF_USAGE'
Usage:
  ./tools/pi5_p25_run_with_log.sh [--label NAME] [--log-dir DIR] -- COMMAND [ARG ...]

Examples:
  ./tools/pi5_p25_run_with_log.sh --label status -- curl -s http://127.0.0.1:8070/api/status
  ./tools/pi5_p25_run_with_log.sh --label http_probe -- ./tools/pi5_p25_op25_http_runtime_probe.sh --seconds 30 --interval 1 --yes

The helper tees stdout/stderr to an upload-ready transcript and exits with the command status.
It prints both the absolute log directory and the absolute log file path.
EOF_USAGE
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --label)
      shift
      if [[ "$#" -eq 0 ]]; then
        echo "FAIL: --label requires a value" >&2
        usage >&2
        exit 2
      fi
      LABEL="$1"
      shift
      ;;
    --log-dir)
      shift
      if [[ "$#" -eq 0 ]]; then
        echo "FAIL: --log-dir requires a value" >&2
        usage >&2
        exit 2
      fi
      LOG_DIR="$1"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

if [[ "$#" -eq 0 ]]; then
  echo "FAIL: no command supplied" >&2
  usage >&2
  exit 2
fi

SAFE_LABEL="$(printf '%s' "$LABEL" | tr -c 'A-Za-z0-9_.-' '_' | sed 's/^_*//; s/_*$//')"
if [[ -z "$SAFE_LABEL" ]]; then
  SAFE_LABEL="command"
fi

case "$LOG_DIR" in
  /*) ;;
  *) LOG_DIR="$(pwd)/$LOG_DIR" ;;
esac

if ! mkdir -p "$LOG_DIR"; then
  echo "FAIL: could not create log directory: $LOG_DIR" >&2
  exit 1
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/${SAFE_LABEL}_${STAMP}.txt"

if ! : > "$LOG_FILE"; then
  echo "FAIL: could not create log file: $LOG_FILE" >&2
  exit 1
fi

exec > >(tee -a "$LOG_FILE") 2>&1

START_EPOCH="$(date +%s)"
echo "=== scanner upload-ready command log ==="
echo "Log directory: $LOG_DIR"
echo "Log file: $LOG_FILE"
echo "Started UTC: $STAMP"
echo "Working directory: $(pwd)"
echo "Command: $*"
echo

"$@"
STATUS=$?
END_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
END_EPOCH="$(date +%s)"
DURATION=$((END_EPOCH - START_EPOCH))

echo
echo "Completed UTC: $END_STAMP"
echo "Duration seconds: $DURATION"
echo "Command exit status: $STATUS"
echo "Log directory: $LOG_DIR"
echo "Log file: $LOG_FILE"
if [[ -f "$LOG_FILE" ]]; then
  echo "Log file verified: yes"
  if command -v ls >/dev/null 2>&1; then
    ls -lh "$LOG_FILE" 2>/dev/null || true
  fi
else
  echo "Log file verified: no"
fi

echo "Upload note: if this ran on the Pi, copy this absolute path to your workstation before uploading."
echo "MSYS2 pull helper: ./tools/msys2_pull_latest_p25_log.sh --host pi@PI-SDR"
if [[ "$STATUS" -eq 0 ]]; then
  echo "FINAL: PASS"
else
  echo "FINAL: FAIL"
fi
exit "$STATUS"
