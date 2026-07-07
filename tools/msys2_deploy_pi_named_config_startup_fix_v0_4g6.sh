#!/usr/bin/env bash
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="${LOG_DIR}/deploy_v0_4g6_named_config_startup_fix_${STAMP}.txt"
mkdir -p "$LOG_DIR" 2>/dev/null || true
exec > >(tee "$LOG_FILE") 2>&1

pass() { printf 'PASS: %s\n' "$*"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { printf 'WARN: %s\n' "$*"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { printf 'FAIL: %s\n' "$*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
summary_and_exit() {
  local rc="${1:-0}"
  printf 'UPLOAD_FILE_MSYS=%s\n' "$LOG_FILE"
  printf 'UPLOAD_FILE_WINDOWS=%s\n' "$(printf '%s' "$LOG_FILE" | sed 's#^/c#C:#; s#/#\\#g')"
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT"
  if [[ "$FAIL_COUNT" -eq 0 && "$rc" -eq 0 ]]; then
    printf 'FINAL: PASS\n'
    exit 0
  fi
  printf 'FINAL: FAIL\n'
  exit 1
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; fi; summary_and_exit $rc' EXIT

printf '=== Deploy V0.4G6 named config startup fix ===\n'

if [[ -d .git && -f DEV_GUARDRAILS.md && -f src/pi_p25_scanner/config_store.py ]]; then
  pass "repo root detected"
else
  fail "run from PI-P25-SCANNER repository root"
  exit 1
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  pass "loaded .env"
else
  warn ".env not found; using defaults/key auth if available"
fi

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-PI-SDR}"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
PI_PASSWORD_VALUE="${PI_PASSWORD:-${SSHPASS:-}}"
REMOTE="${PI_USER}@${PI_HOST}"
REMOTE_TARBALL="/tmp/pi_p25_v0_4g6_named_config_startup_fix_${STAMP}.tar.gz"
LOCAL_TARBALL="/tmp/pi_p25_v0_4g6_named_config_startup_fix_${STAMP}.tar.gz"

if command -v sshpass >/dev/null 2>&1 && [[ -n "$PI_PASSWORD_VALUE" ]]; then
  SSH_BASE=(sshpass -p "$PI_PASSWORD_VALUE" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
  SCP_BASE=(sshpass -p "$PI_PASSWORD_VALUE" scp -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
  pass "using sshpass for Pi connection"
else
  SSH_BASE=(ssh -o StrictHostKeyChecking=no)
  SCP_BASE=(scp -O -o StrictHostKeyChecking=no)
  warn "using ssh/scp without sshpass; key auth or interactive auth must work"
fi

tar -czf "$LOCAL_TARBALL" src/pi_p25_scanner/config_store.py
pass "created deploy tarball: $LOCAL_TARBALL"

"${SCP_BASE[@]}" "$LOCAL_TARBALL" "${REMOTE}:${REMOTE_TARBALL}"
pass "copied deploy tarball to ${REMOTE}:${REMOTE_TARBALL}"

"${SSH_BASE[@]}" "$REMOTE" \
  "PI_REPO='${PI_REPO}' REMOTE_TARBALL='${REMOTE_TARBALL}' bash -s" <<'REMOTE_SH'
set -Eeuo pipefail
cd "$PI_REPO"
printf 'REMOTE_REPO=%s\n' "$PI_REPO"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p runtime/settings/backups
cp src/pi_p25_scanner/config_store.py "runtime/settings/backups/config_store.py.before_v0_4g6_${STAMP}"
tar -xzf "$REMOTE_TARBALL"
python3 -m py_compile src/pi_p25_scanner/config_store.py src/pi_p25_scanner/backend.py
PYTHONPATH=src python3 - <<'PY'
from pi_p25_scanner.config_store import active_config_metadata, list_named_configs
payload = list_named_configs()
print("LIST_NAMED_CONFIGS_TYPE", type(payload).__name__)
meta = active_config_metadata()
print("ACTIVE_CONFIG_METADATA_OK", meta.get("named_config_count"))
PY
sudo systemctl reset-failed pi-p25-scanner.service || true
sudo systemctl restart pi-p25-scanner.service
REMOTE_SH
pass "remote config_store patched and service restart requested"

probe_url() {
  local url="$1"
  local label="$2"
  local attempts="${3:-45}"
  local delay="${4:-1}"
  local n
  for ((n=1; n<=attempts; n++)); do
    if "${SSH_BASE[@]}" "$REMOTE" "python3 - <<'PY'
import urllib.request
url = '$url'
with urllib.request.urlopen(url, timeout=1.5) as response:
    data = response.read(4096)
    print(response.status)
    print(data[:300].decode('utf-8', 'replace'))
PY" >/tmp/pi_p25_v0_4g6_probe.txt 2>/tmp/pi_p25_v0_4g6_probe.err; then
      printf 'PROBE_OK %s %s\n' "$label" "$url"
      cat /tmp/pi_p25_v0_4g6_probe.txt
      return 0
    fi
    sleep "$delay"
  done
  printf 'PROBE_FAIL %s %s\n' "$label" "$url"
  cat /tmp/pi_p25_v0_4g6_probe.err 2>/dev/null || true
  return 1
}

if probe_url "http://127.0.0.1:8070/api/status" "status" 60 1; then
  pass "backend /api/status reachable"
else
  fail "backend /api/status not reachable after restart"
fi

if probe_url "http://127.0.0.1:8070/api/config/named" "named-config-api" 20 1; then
  pass "named config API reachable"
else
  fail "named config API not reachable"
fi

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  printf 'SERVICE_STATUS_BEGIN\n'
  "${SSH_BASE[@]}" "$REMOTE" "systemctl --no-pager -l status pi-p25-scanner.service || true"
  printf 'JOURNAL_BEGIN\n'
  "${SSH_BASE[@]}" "$REMOTE" "journalctl -u pi-p25-scanner.service -n 160 --no-pager || true"
  exit 1
fi

LAN_IP="$("${SSH_BASE[@]}" "$REMOTE" "hostname -I | awk '{print \$1}'" 2>/dev/null || true)"
if [[ -n "$LAN_IP" ]]; then
  printf 'DASHBOARD_URL=http://%s:8070\n' "$LAN_IP"
  printf 'AUDIO_URL=http://%s:8072/audio.wav\n' "$LAN_IP"
else
  printf 'DASHBOARD_URL=http://%s:8070\n' "$PI_HOST"
fi
