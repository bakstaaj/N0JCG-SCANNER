#!/usr/bin/env bash
set -u
PATCH_NAME="deploy_v0_5d_emergency_ui_restore"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/${PATCH_NAME}_${STAMP}.txt"
PASS_COUNT=0; WARN_COUNT=0; FAIL_COUNT=0
log(){ printf '%s\n' "$*" | tee -a "$LOG_FILE"; }
pass(){ PASS_COUNT=$((PASS_COUNT+1)); log "PASS: $*"; }
warn(){ WARN_COUNT=$((WARN_COUNT+1)); log "WARN: $*"; }
fail(){ FAIL_COUNT=$((FAIL_COUNT+1)); log "FAIL: $*"; }
finish(){
  log "UPLOAD_FILE_MSYS=$LOG_FILE"
  log "UPLOAD_FILE_WINDOWS=C:\\Users\\jim\\Downloads\\pi-p25-command-logs\\${PATCH_NAME}_${STAMP}.txt"
  log "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [ "$FAIL_COUNT" -eq 0 ]; then log "FINAL: PASS"; else log "FINAL: FAIL"; fi
}
trap 'rc=$?; if [ $rc -ne 0 ]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; fi; finish; exit $rc' EXIT

log "=== Deploy V0.5D emergency UI restore ==="
if [ ! -d web ] || [ ! -f web/index.html ] || [ ! -f web/app.js ]; then fail "run from scanner repo root after applying V0.5D"; exit 1; fi
pass "local UI files found"

if [ -f .env ]; then set -a; . ./.env; set +a; pass "loaded .env"; else warn ".env not found"; fi
PI_HOST="192.168.254.63"; PI_USER="${PI_USER:-pi}"; PI_ROOT="${PI_ROOT:-/home/pi/n0jcg-scanner}"
TARGET="${PI_USER}@${PI_HOST}"
pass "target fixed to ${TARGET}:${PI_ROOT}"

SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
SCP_BASE=(scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [ -n "${SSHPASS:-}" ]; then
  export SSHPASS
  SSH_CMD=(sshpass -e "${SSH_BASE[@]}")
  SCP_CMD=(sshpass -e "${SCP_BASE[@]}")
  pass "using sshpass with SSHPASS"
elif [ -n "${PI_PASSWORD:-}" ]; then
  export SSHPASS="$PI_PASSWORD"
  SSH_CMD=(sshpass -e "${SSH_BASE[@]}")
  SCP_CMD=(sshpass -e "${SCP_BASE[@]}")
  pass "using sshpass with PI_PASSWORD"
else
  SSH_CMD=("${SSH_BASE[@]}" -o BatchMode=yes)
  SCP_CMD=("${SCP_BASE[@]}" -o BatchMode=yes)
  warn "PI_PASSWORD/SSHPASS not set; trying SSH key auth only"
fi

"${SSH_CMD[@]}" "$TARGET" "cd '$PI_ROOT' && test -d web && test -f src/pi_p25_scanner/backend.py" >>"$LOG_FILE" 2>&1 || { fail "Pi repo not reachable without interactive prompt"; exit 1; }
pass "Pi repo reachable without interactive prompt"

TMP_TAR="runtime/${PATCH_NAME}_${STAMP}.tar.gz"
mkdir -p runtime
tar -czf "$TMP_TAR" web/index.html web/app.js
pass "built deploy tar"
"${SCP_CMD[@]}" "$TMP_TAR" "$TARGET:/tmp/${PATCH_NAME}.tar.gz" >>"$LOG_FILE" 2>&1 || { fail "copy deploy tar failed"; exit 1; }
pass "copied deploy tar to Pi"

"${SSH_CMD[@]}" "$TARGET" "cd '$PI_ROOT' && mkdir -p runtime/patch_backups/v0_5d_emergency_ui_restore_${STAMP} && cp -f web/index.html runtime/patch_backups/v0_5d_emergency_ui_restore_${STAMP}/index.html.bak 2>/dev/null || true && cp -f web/app.js runtime/patch_backups/v0_5d_emergency_ui_restore_${STAMP}/app.js.bak 2>/dev/null || true && tar -xzf /tmp/${PATCH_NAME}.tar.gz -C '$PI_ROOT' && python3 -m py_compile src/pi_p25_scanner/backend.py" >>"$LOG_FILE" 2>&1 || { fail "remote deploy to project web failed"; exit 1; }
pass "deployed emergency UI to Pi project web path"

LIVE_WEB_ROOT=$("${SSH_CMD[@]}" "$TARGET" "cd '$PI_ROOT' && python3 - <<'REMOTE_PY'
from pathlib import Path
try:
    from pi_p25_scanner import backend
    print(Path(backend.WEB_ROOT).resolve())
except Exception:
    print(Path('web').resolve())
REMOTE_PY" 2>>"$LOG_FILE" | tail -n 1 | tr -d '\r')
if [ -z "$LIVE_WEB_ROOT" ]; then LIVE_WEB_ROOT="$PI_ROOT/web"; fi
log "LIVE_WEB_ROOT=$LIVE_WEB_ROOT"
if [ "$LIVE_WEB_ROOT" != "$PI_ROOT/web" ]; then
  "${SSH_CMD[@]}" "$TARGET" "mkdir -p '$LIVE_WEB_ROOT' && cp -f '$PI_ROOT/web/index.html' '$LIVE_WEB_ROOT/index.html' && cp -f '$PI_ROOT/web/app.js' '$LIVE_WEB_ROOT/app.js'" >>"$LOG_FILE" 2>&1 || { fail "copy to live WEB_ROOT failed"; exit 1; }
  pass "also copied emergency UI to live WEB_ROOT"
else
  pass "live WEB_ROOT is project web path"
fi

# Static files are read from disk on request; avoid restarting backend unless HTTP is down.
if ! curl -fsS --max-time 5 "http://${PI_HOST}:8070/api/status" >/tmp/v0_5d_status.json 2>>"$LOG_FILE"; then
  warn "backend HTTP was not responding; starting backend safely"
  "${SSH_CMD[@]}" "$TARGET" "cd '$PI_ROOT' && mkdir -p runtime/logs && nohup python3 -m pi_p25_scanner.backend --host 0.0.0.0 --port 8070 > runtime/logs/backend_v0_5d_restore.log 2>&1 &" >>"$LOG_FILE" 2>&1 || true
  sleep 3
fi

APP_JS="$(curl -fsS --max-time 10 "http://${PI_HOST}:8070/app.js?v=v0.5d-emergency-ui-restore" 2>>"$LOG_FILE" || true)"
INDEX_HTML="$(curl -fsS --max-time 10 "http://${PI_HOST}:8070/index.html?cachebust=v0.5d" 2>>"$LOG_FILE" || true)"
if printf '%s' "$APP_JS" | grep -q 'V0.5D_EMERGENCY_UI_RESTORE'; then pass "verified V0.5D marker is served by /app.js"; else fail "V0.5D marker was not served by /app.js"; exit 1; fi
if printf '%s' "$INDEX_HTML" | grep -q 'v0.5d-emergency-ui-restore'; then pass "verified V0.5D cache-busted app.js reference is served by /index.html"; else fail "V0.5D index marker was not served"; exit 1; fi
curl -fsS --max-time 5 "http://${PI_HOST}:8070/api/status" >/tmp/v0_5d_status.json 2>>"$LOG_FILE" && pass "backend status endpoint responded after deploy" || { fail "backend status endpoint failed after deploy"; exit 1; }
pass "V0.5D emergency UI restore deployed"
