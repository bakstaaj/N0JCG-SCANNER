#!/usr/bin/env bash
set -u
PATCH_NAME="deploy_v0_5c1_unified_radio_setup"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
if [[ ! -d "$LOG_DIR" ]]; then LOG_DIR="$HOME/pi-p25-command-logs"; fi
mkdir -p "$LOG_DIR" 2>/dev/null || true
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/${PATCH_NAME}_${STAMP}.txt"
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
log(){ printf '%s\n' "$*" | tee -a "$LOG_FILE"; }
pass(){ PASS_COUNT=$((PASS_COUNT+1)); log "PASS: $*"; }
warn(){ WARN_COUNT=$((WARN_COUNT+1)); log "WARN: $*"; }
fail(){ FAIL_COUNT=$((FAIL_COUNT+1)); log "FAIL: $*"; }
finish(){
  local final="PASS"; [[ "$FAIL_COUNT" -ne 0 ]] && final="FAIL"
  log "UPLOAD_FILE_MSYS=$LOG_FILE"
  case "$LOG_FILE" in /c/*) log "UPLOAD_FILE_WINDOWS=$(printf '%s' "$LOG_FILE" | sed -E 's#^/c/#C:\\\\#; s#/#\\\\#g')";; esac
  log "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  log "FINAL: $final"
  [[ "$final" == PASS ]]
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish || true; exit $rc; fi' ERR
log "=== Deploy V0.5C1 force unified Radio Setup UI ==="
if [[ -f .env ]]; then set -a; source .env; set +a; pass "loaded .env"; else warn ".env not found"; fi
PI_HOST="192.168.254.63"
PI_USER="${PI_USER:-pi}"
PI_PATH="/home/pi/n0jcg-scanner"
REMOTE="${PI_USER}@${PI_HOST}"
pass "target fixed to ${REMOTE}:${PI_PATH}"
SSH_BASE=(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8)
SCP_BASE=(scp -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8)
if [[ -n "${PI_PASSWORD:-}" ]]; then export SSHPASS="$PI_PASSWORD"; fi
if [[ -n "${SSHPASS:-}" ]]; then
  SSH=(sshpass -e "${SSH_BASE[@]}")
  SCP=(sshpass -e "${SCP_BASE[@]}")
  pass "using sshpass with PI_PASSWORD/SSHPASS"
else
  SSH=("${SSH_BASE[@]}" -o BatchMode=yes)
  SCP=("${SCP_BASE[@]}" -o BatchMode=yes)
  warn "PI_PASSWORD/SSHPASS not set; trying SSH key auth only"
fi
if ! "${SSH[@]}" "$REMOTE" "test -d '$PI_PATH'" >> "$LOG_FILE" 2>&1; then
  fail "Pi repo not reachable without an interactive password; set PI_PASSWORD or SSHPASS in .env/shell"
  finish || exit 1
fi
pass "Pi repo reachable without interactive prompt"
TAR="runtime/${PATCH_NAME}_${STAMP}.tgz"
mkdir -p runtime
tar -czf "$TAR" web/app.js web/index.html
pass "built deploy tar"
"${SCP[@]}" "$TAR" "$REMOTE:/tmp/${PATCH_NAME}.tgz" >> "$LOG_FILE" 2>&1
pass "copied deploy tar to Pi"
"${SSH[@]}" "$REMOTE" "set -e; cd '$PI_PATH'; mkdir -p runtime/patch_backups/v0_5c_${STAMP}; cp -p web/app.js web/index.html runtime/patch_backups/v0_5c_${STAMP}/ 2>/dev/null || true; tar -xzf /tmp/${PATCH_NAME}.tgz -C '$PI_PATH'; python3 -m py_compile src/pi_p25_scanner/backend.py; if systemctl --user list-unit-files | grep -q '^pi-p25-scanner.service'; then systemctl --user restart pi-p25-scanner.service; else pkill -f 'pi_p25_scanner.backend' || true; nohup python3 -m pi_p25_scanner.backend --host 0.0.0.0 --port 8070 > runtime/backend.log 2>&1 & fi; sleep 2" >> "$LOG_FILE" 2>&1
pass "deployed UI files and restarted backend"
APP_FETCH="$(curl -fsS "http://${PI_HOST}:8070/app.js?verify=v0_5c_${STAMP}" 2>>"$LOG_FILE" || true)"
if printf '%s' "$APP_FETCH" | grep -q 'V0.5C_UNIFIED_RADIO_SETUP'; then
  pass "verified V0.5C marker is served by /app.js"
else
  fail "V0.5C marker was not served by /app.js"
  finish || exit 1
fi
INDEX_FETCH="$(curl -fsS "http://${PI_HOST}:8070/index.html?verify=v0_5c_${STAMP}" 2>>"$LOG_FILE" || true)"
if printf '%s' "$INDEX_FETCH" | grep -q 'v=0.5c-unified-radio-setup'; then
  pass "verified cache-busted app.js reference is served by /index.html"
else
  fail "cache-busted app.js reference not found in served index.html"
  finish || exit 1
fi
STATUS="$(curl -fsS "http://${PI_HOST}:8070/api/status" 2>>"$LOG_FILE" || true)"
if printf '%s' "$STATUS" | grep -q 'scanner_state'; then
  pass "backend status endpoint responded after deploy"
else
  warn "backend status endpoint response did not include scanner_state"
fi
finish
