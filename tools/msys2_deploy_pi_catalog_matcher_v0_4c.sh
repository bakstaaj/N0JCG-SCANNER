#!/usr/bin/env bash
# Deploy V0.4C catalog matcher UI files to the Pi.
set -Eeuo pipefail
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
REPORT_FILE="$LOG_DIR/deploy_catalog_matcher_v0_4c_${STAMP}.txt"
TMP_TARBALL="/tmp/pi_p25_v0_4c_catalog_matcher_${STAMP}.tgz"
REMOTE_TARBALL="/tmp/pi_p25_v0_4c_catalog_matcher_${STAMP}.tgz"
REMOTE_SCRIPT_LOCAL="/tmp/pi_p25_v0_4c_catalog_remote_${STAMP}.sh"
REMOTE_SCRIPT="/tmp/pi_p25_v0_4c_catalog_remote_${STAMP}.sh"
mkdir -p "$LOG_DIR"
: > "$REPORT_FILE"
pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
finish() {
  local windows_path
  windows_path="$(cygpath -w "$REPORT_FILE" 2>/dev/null || printf '%s' "$REPORT_FILE")"
  printf 'UPLOAD_FILE_MSYS=%s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
  printf 'UPLOAD_FILE_WINDOWS=%s\n' "$windows_path" | tee -a "$REPORT_FILE"
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"; exit 0; fi
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"; exit 1
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish; fi' ERR

printf '=== PI-P25-SCANNER V0.4C catalog matcher deploy ===\n' | tee -a "$REPORT_FILE"
if [[ -f .env ]]; then set -a; . ./.env; set +a; pass "loaded .env"; else warn ".env not found; using defaults/env"; fi
PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-PI-SDR}"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
PI_PASSWORD="${PI_PASSWORD:-${SSHPASS:-}}"
if [[ -z "$PI_PASSWORD" ]]; then fail "PI_PASSWORD or SSHPASS missing in .env/environment"; finish; fi
for cmd in sshpass ssh scp tar python3; do
  if command -v "$cmd" >/dev/null 2>&1; then pass "command available: $cmd"; else fail "missing required command: $cmd"; fi
done
if [[ "$FAIL_COUNT" -ne 0 ]]; then finish; fi

required_files=(web/app.js web/system_catalog.example.json)
for path in "${required_files[@]}"; do [[ -f "$path" ]] && pass "deploy file exists: $path" || fail "missing deploy file: $path"; done
if [[ "$FAIL_COUNT" -ne 0 ]]; then finish; fi
python3 -m json.tool web/system_catalog.example.json >/dev/null
pass "local catalog JSON parses"
if command -v node >/dev/null 2>&1; then node --check web/app.js >>"$REPORT_FILE" 2>&1 && pass "local app.js syntax passed" || fail "local app.js syntax failed"; else warn "node unavailable locally"; fi
if [[ "$FAIL_COUNT" -ne 0 ]]; then finish; fi

tar -czf "$TMP_TARBALL" "${required_files[@]}"
pass "created deploy tarball: $TMP_TARBALL"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -o PreferredAuthentications=password,keyboard-interactive,publickey)
SSH=(sshpass -p "$PI_PASSWORD" ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}")
SCP=(sshpass -p "$PI_PASSWORD" scp -O "${SSH_OPTS[@]}")
cat > "$REMOTE_SCRIPT_LOCAL" <<REMOTE
#!/usr/bin/env bash
set -Eeuo pipefail
REPO='$PI_REPO'
TARBALL='$REMOTE_TARBALL'
printf 'Remote deploy repo: %s\n' "\$REPO"
cd "\$REPO"
tar -xzf "\$TARBALL"
python3 -m json.tool web/system_catalog.example.json >/dev/null
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
else
  grep -q 'STATE_ALIASES' web/app.js
  grep -q 'TOPAZ / TRWC Mesa Simulcast Starter' web/system_catalog.example.json
  printf 'WARN: node unavailable on Pi; marker checks passed\n'
fi
if systemctl list-unit-files pi-p25-scanner.service >/dev/null 2>&1; then
  printf '%s\n' '$PI_PASSWORD' | sudo -S systemctl restart pi-p25-scanner.service
  printf 'PASS: restarted pi-p25-scanner.service\n'
else
  printf 'WARN: pi-p25-scanner.service not found; backend not restarted\n'
fi
python3 - <<'PY'
import json, time, urllib.request
for _ in range(45):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8070/system_catalog.example.json', timeout=2) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        assert payload.get('catalog_version') == 2
        print('PROBE_OK catalog_version=2 systems=%s' % len(payload.get('systems', [])))
        break
    except Exception as exc:
        last = exc
        time.sleep(1)
else:
    print('PROBE_FAIL catalog', type(last).__name__, last)
    raise SystemExit(1)
PY
REMOTE
chmod +x "$REMOTE_SCRIPT_LOCAL"
pass "created remote deploy script"
"${SCP[@]}" "$TMP_TARBALL" "${PI_USER}@${PI_HOST}:${REMOTE_TARBALL}" >>"$REPORT_FILE" 2>&1
pass "copied deploy tarball"
"${SCP[@]}" "$REMOTE_SCRIPT_LOCAL" "${PI_USER}@${PI_HOST}:${REMOTE_SCRIPT}" >>"$REPORT_FILE" 2>&1
pass "copied remote deploy script"
"${SSH[@]}" "bash '$REMOTE_SCRIPT'" 2>&1 | tee -a "$REPORT_FILE"
pass "remote deploy completed"
"${SSH[@]}" "rm -f '$REMOTE_TARBALL' '$REMOTE_SCRIPT'" >>"$REPORT_FILE" 2>&1 || warn "remote cleanup failed"
rm -f "$TMP_TARBALL" "$REMOTE_SCRIPT_LOCAL" || true
pass "local temp files cleaned"
printf '\nTry wizard search examples after hard refresh:\n' | tee -a "$REPORT_FILE"
printf '  State: AZ, County: Maricopa, City: Mesa\n' | tee -a "$REPORT_FILE"
printf '  State: Arizona, County: Maricopa, City: Gilbert\n' | tee -a "$REPORT_FILE"
printf '  State: AZ, County: blank, City: TOPAZ\n' | tee -a "$REPORT_FILE"
finish
