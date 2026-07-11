#!/usr/bin/env bash
set -Eeuo pipefail
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
LOG_FILE="$LOG_DIR/run_v0_5a1_ppm_auto_calibration_${STAMP}.txt"
mkdir -p "$LOG_DIR" 2>/dev/null || true
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
log(){ printf '%s\n' "$*" | tee -a "$LOG_FILE"; }
pass(){ PASS_COUNT=$((PASS_COUNT+1)); log "PASS: $*"; }
warn(){ WARN_COUNT=$((WARN_COUNT+1)); log "WARN: $*"; }
fail(){ FAIL_COUNT=$((FAIL_COUNT+1)); log "FAIL: $*"; }
finish(){ log "UPLOAD_FILE_MSYS=$LOG_FILE"; log "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || printf '%s' "$LOG_FILE")"; log "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"; [[ $FAIL_COUNT -eq 0 ]] && log "FINAL: PASS" || log "FINAL: FAIL"; }
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "PPM calibration run aborted unexpectedly at line $LINENO rc=$rc"; fi; finish; exit $rc' EXIT

log "=== Run V0.5A1 automated PPM calibration ==="
[[ -f .env ]] && { set -a; source ./.env; set +a; pass "loaded .env"; }
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
SPAN_PPM="${1:-3}"
STEP_PPM="${2:-1}"
DWELL_SECONDS="${3:-8}"
APPLY_VOICE="${4:-false}"
SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
if [[ -n "${SSHPASS:-}" ]] && command -v sshpass >/dev/null 2>&1; then
  SSH=(sshpass -e "${SSH_BASE[@]}")
  pass "using sshpass with SSHPASS"
elif [[ -n "${PI_PASSWORD:-}" ]] && command -v sshpass >/dev/null 2>&1; then
  export SSHPASS="$PI_PASSWORD"
  SSH=(sshpass -e "${SSH_BASE[@]}")
  pass "using sshpass with PI_PASSWORD"
else
  SSH=("${SSH_BASE[@]}" -o BatchMode=yes)
  warn "no SSHPASS/PI_PASSWORD available; using SSH key auth only and will not prompt"
fi

body=$(python3 - <<PY
import json
print(json.dumps({"span_ppm": int("$SPAN_PPM"), "step_ppm": int("$STEP_PPM"), "dwell_seconds": int("$DWELL_SECONDS"), "apply_voice": str("$APPLY_VOICE").lower() in ("1","true","yes")}))
PY
)
timeout_seconds=$(( ( (SPAN_PPM * 2 / STEP_PPM) + 2 ) * DWELL_SECONDS + 60 ))
pass "requesting calibration span=${SPAN_PPM} step=${STEP_PPM} dwell=${DWELL_SECONDS}s timeout=${timeout_seconds}s"

"${SSH[@]}" "${PI_USER}@${PI_HOST}" "curl -fsS --max-time $timeout_seconds -H 'Content-Type: application/json' -d '$body' http://127.0.0.1:8070/api/calibration/ppm/run" | tee "$LOG_FILE.tmp"
cat "$LOG_FILE.tmp" | python3 -m json.tool | tee -a "$LOG_FILE"
rm -f "$LOG_FILE.tmp"
pass "PPM calibration completed"
