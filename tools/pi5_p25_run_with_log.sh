#!/usr/bin/env bash
set -u

LABEL="command"
LOG_DIR="${P25_COMMAND_LOG_DIR:-.p25_command_logs}"

usage() {
  cat <<'EOF_USAGE'
Usage:
  ./tools/pi5_p25_run_with_log.sh [--label NAME] -- COMMAND [ARG ...]

Examples:
  ./tools/pi5_p25_run_with_log.sh --label status -- curl -s http://127.0.0.1:8070/api/status
  ./tools/pi5_p25_run_with_log.sh --label http_probe -- ./tools/pi5_p25_op25_http_runtime_probe.sh --seconds 30 --interval 1 --yes

The helper tees stdout/stderr to .p25_command_logs/<label>_<utc_timestamp>.txt and exits with the command status.
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
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/${SAFE_LABEL}_${STAMP}.txt"

exec > >(tee -a "$LOG_FILE") 2>&1

START_EPOCH="$(date +%s)"
echo "=== PI-P25-SCANNER upload-ready command log ==="
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
echo "Log file: $LOG_FILE"
if [[ "$STATUS" -eq 0 ]]; then
  echo "FINAL: PASS"
else
  echo "FINAL: FAIL"
fi
exit "$STATUS"
