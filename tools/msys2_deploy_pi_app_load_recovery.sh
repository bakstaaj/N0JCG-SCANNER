#!/usr/bin/env bash
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/v0_3q_deploy_app_load_recovery_${STAMP}.txt"
PI_HOST="${PI_HOST:-PI-SDR}"
PI_USER="${PI_USER:-pi}"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
SSH_TARGET="${PI_USER}@${PI_HOST}"

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"
pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
finish() {
  printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
    exit 0
  fi
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
}

printf '=== scanner V0.3Q deploy app-load recovery ===\n' | tee -a "$REPORT_FILE"
printf 'Started UTC: %s\n' "$STAMP" | tee -a "$REPORT_FILE"
printf 'SSH target: %s\n' "$SSH_TARGET" | tee -a "$REPORT_FILE"

if [[ -f tools/msys2_env_common.sh ]]; then
  # shellcheck disable=SC1091
  source tools/msys2_env_common.sh
  pass "loaded tools/msys2_env_common.sh"
elif [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  pass "loaded .env"
else
  warn "no .env/helper found; relying on existing ssh auth or environment"
fi

SSH_CMD=(ssh -o StrictHostKeyChecking=accept-new)
if [[ -n "${PI_PASSWORD:-}" ]] && command -v sshpass >/dev/null 2>&1; then
  SSH_CMD=(sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=accept-new)
  pass "using sshpass authentication"
else
  warn "PI_PASSWORD not available or sshpass missing; using default ssh auth"
fi

if "${SSH_CMD[@]}" "$SSH_TARGET" "cd '$PI_REPO' && git pull --ff-only && python3 -m py_compile src/pi_p25_scanner/backend.py && python3 -m py_compile tools/pi5_p25_browser_audio_raw_bridge_server.py" | tee -a "$REPORT_FILE"; then
  pass "Pi repository pulled and Python compile checks passed"
else
  fail "Pi repository pull or compile check failed"
  finish
fi

REMOTE_RESTART='set -Eeuo pipefail
cd "'$PI_REPO'"
if command -v sudo >/dev/null 2>&1 && systemctl list-unit-files pi-p25-scanner.service >/dev/null 2>&1; then
  sudo systemctl restart pi-p25-scanner.service
else
  pkill -f "pi_p25_scanner.backend" >/dev/null 2>&1 || true
  nohup env PYTHONPATH=src python3 -m pi_p25_scanner.backend --host 0.0.0.0 --port 8070 > runtime/logs/backend_v0_3q_recovery.log 2>&1 &
fi
for i in 1 2 3 4 5 6 7 8 9 10; do
  if python3 - <<"PY" >/dev/null 2>&1
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8070/api/status", timeout=3) as resp:
    data=json.loads(resp.read().decode("utf-8"))
assert isinstance(data, dict)
PY
  then
    echo "PASS_REMOTE_API_STATUS=1"
    exit 0
  fi
  sleep 1
done
echo "FAIL_REMOTE_API_STATUS=1"
if command -v systemctl >/dev/null 2>&1; then systemctl --no-pager --full status pi-p25-scanner.service || true; fi
exit 1'

if "${SSH_CMD[@]}" "$SSH_TARGET" "$REMOTE_RESTART" | tee -a "$REPORT_FILE"; then
  pass "Pi backend restarted and /api/status responds"
else
  fail "Pi backend restart or /api/status validation failed"
  finish
fi

if "${SSH_CMD[@]}" "$SSH_TARGET" "hostname -I | awk '{print \$1}'" | tee -a "$REPORT_FILE"; then
  pass "printed Pi LAN IP"
else
  warn "could not print Pi LAN IP"
fi

printf 'Open: http://%s:8070\n' "$PI_HOST" | tee -a "$REPORT_FILE"
finish
