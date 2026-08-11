#!/usr/bin/env bash
# Deploy V0.4D RadioReference UI/API integration to the Pi from MSYS2.
set -Eeuo pipefail
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
REPORT_FILE="$LOG_DIR/deploy_radioreference_v0_4d_${STAMP}.txt"
TMP_TARBALL="/tmp/pi_p25_v0_4d_radioreference_${STAMP}.tgz"
REMOTE_TARBALL="/tmp/pi_p25_v0_4d_radioreference_${STAMP}.tgz"
REMOTE_SCRIPT_LOCAL="/tmp/pi_p25_v0_4d_remote_${STAMP}.sh"
REMOTE_SCRIPT="/tmp/pi_p25_v0_4d_remote_${STAMP}.sh"
PI_HOST_ARG=""
PI_USER_ARG=""
PI_REPO_ARG=""
SKIP_DEPS=0
mkdir -p "$LOG_DIR"
: > "$REPORT_FILE"
pass(){ printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT+1)); }
finish(){ local wp; wp="$(cygpath -w "$REPORT_FILE" 2>/dev/null || printf '%s' "$REPORT_FILE")"; printf 'UPLOAD_FILE_MSYS=%s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"; printf 'UPLOAD_FILE_WINDOWS=%s\n' "$wp" | tee -a "$REPORT_FILE"; printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"; [[ "$FAIL_COUNT" -eq 0 ]] && { printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"; exit 0; }; printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"; exit 1; }
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish; fi' ERR
usage(){ cat <<USAGE
Usage: ./tools/msys2_deploy_pi_radioreference_v0_4d.sh [--host PI-SDR] [--user pi] [--repo /home/pi/n0jcg-scanner] [--skip-deps]
USAGE
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) shift; PI_HOST_ARG="$1"; shift ;;
    --user) shift; PI_USER_ARG="$1"; shift ;;
    --repo) shift; PI_REPO_ARG="$1"; shift ;;
    --skip-deps) SKIP_DEPS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done
printf '=== scanner V0.4D RadioReference deploy ===\n' | tee -a "$REPORT_FILE"
if [[ -f DEV_GUARDRAILS.md && -d src/pi_p25_scanner && -d web && -d tools ]]; then pass "running from repo root"; else fail "run from repo root"; finish; fi
if [[ -f .env ]]; then set -a; . ./.env; set +a; pass "loaded .env"; else warn "no .env found"; fi
PI_HOST="${PI_HOST_ARG:-${PI_HOST:-PI-SDR}}"
PI_USER="${PI_USER_ARG:-${PI_USER:-pi}}"
PI_REPO="${PI_REPO_ARG:-${PI_REPO:-/home/pi/n0jcg-scanner}}"
PI_PASSWORD="${PI_PASSWORD:-${SSHPASS:-}}"
if [[ -z "$PI_PASSWORD" ]]; then fail "PI_PASSWORD missing in .env"; finish; fi
for cmd in sshpass ssh scp tar python3; do command -v "$cmd" >/dev/null 2>&1 && pass "command available: $cmd" || fail "missing command: $cmd"; done
[[ "$FAIL_COUNT" -eq 0 ]] || finish
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_import.py >>"$REPORT_FILE" 2>&1 && pass "local backend/import compile passed" || fail "local python compile failed"
if command -v node >/dev/null 2>&1; then node --check web/app.js >>"$REPORT_FILE" 2>&1 && pass "local app.js syntax passed" || fail "local app.js syntax failed"; else warn "node unavailable locally; skipped app.js syntax"; fi
bash -n tools/pi5_p25_install_radioreference_deps.sh >>"$REPORT_FILE" 2>&1 && pass "Pi dependency installer syntax passed" || fail "dependency installer syntax failed"
[[ "$FAIL_COUNT" -eq 0 ]] || finish
FILES=(
  src/pi_p25_scanner/backend.py
  src/pi_p25_scanner/radioreference_import.py
  web/index.html
  web/app.js
  web/app.css
  tools/pi5_p25_install_radioreference_deps.sh
)
tar -czf "$TMP_TARBALL" "${FILES[@]}"
pass "created deploy tarball: $TMP_TARBALL"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts" -o PreferredAuthentications=password,keyboard-interactive,publickey)
SSH=(sshpass -p "$PI_PASSWORD" ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}")
SCP=(sshpass -p "$PI_PASSWORD" scp -O "${SSH_OPTS[@]}")
REMOTE_PASSWORD_B64="$(printf '%s' "$PI_PASSWORD" | base64 | tr -d '\n')"
cat > "$REMOTE_SCRIPT_LOCAL" <<REMOTE
#!/usr/bin/env bash
set -Eeuo pipefail
REPO='$PI_REPO'
TARBALL='$REMOTE_TARBALL'
SKIP_DEPS='$SKIP_DEPS'
export SUDO_PASSWORD="\$(printf '%s' '$REMOTE_PASSWORD_B64' | base64 -d)"
printf 'Remote deploy repo: %s\n' "\$REPO"
cd "\$REPO"
tar -xzf "\$TARBALL"
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/radioreference_import.py
if [[ "\$SKIP_DEPS" -ne 1 ]]; then
  ./tools/pi5_p25_install_radioreference_deps.sh || printf 'WARN: RadioReference dependency installer failed; UI will report zeep unavailable\n'
else
  printf 'WARN: skipped RadioReference dependency install\n'
fi
if systemctl list-unit-files pi-p25-scanner.service >/dev/null 2>&1; then
  printf '%s\n' "\$SUDO_PASSWORD" | sudo -S systemctl restart pi-p25-scanner.service
  printf 'PASS: restarted pi-p25-scanner.service\n'
else
  pkill -f 'src/pi_p25_scanner/backend.py' || true
  nohup python3 src/pi_p25_scanner/backend.py --host 0.0.0.0 --port 8070 > runtime/logs/backend.log 2>&1 &
  printf 'WARN: service unit not found; started backend with nohup\n'
fi
python3 - <<'PY'
import json, time, urllib.request, sys
for _ in range(45):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8070/api/status', timeout=2) as r:
            print('PROBE_OK /api/status', r.status)
        break
    except Exception as exc:
        last = exc
        time.sleep(1)
else:
    print('PROBE_FAIL /api/status', type(last).__name__, last)
    raise SystemExit(1)
for url in ('http://127.0.0.1:8070/api/radioreference/status', '/'):
    with urllib.request.urlopen(url, timeout=3) as r:
        body = r.read(1200).decode('utf-8', errors='replace')
    print('PROBE_OK', url, body[:300].replace('\n', ' '))
PY
LAN_IP="\$(hostname -I 2>/dev/null | awk '{print \$1}' || true)"
printf 'PI_LAN_IP=%s\n' "\$LAN_IP"
REMOTE
chmod +x "$REMOTE_SCRIPT_LOCAL"
"${SCP[@]}" "$TMP_TARBALL" "${PI_USER}@${PI_HOST}:${REMOTE_TARBALL}" >>"$REPORT_FILE" 2>&1
pass "copied deploy tarball to ${PI_USER}@${PI_HOST}:${REMOTE_TARBALL}"
"${SCP[@]}" "$REMOTE_SCRIPT_LOCAL" "${PI_USER}@${PI_HOST}:${REMOTE_SCRIPT}" >>"$REPORT_FILE" 2>&1
pass "copied remote deploy script"
"${SSH[@]}" "bash '$REMOTE_SCRIPT'" 2>&1 | tee -a "$REPORT_FILE"
pass "remote deploy completed"
"${SSH[@]}" "rm -f '$REMOTE_TARBALL' '$REMOTE_SCRIPT'" >>"$REPORT_FILE" 2>&1 || warn "remote cleanup failed"
rm -f "$TMP_TARBALL" "$REMOTE_SCRIPT_LOCAL" || true
pass "local cleanup completed"
printf '\nNext: open http://%s:8070, Menu > Radio Setup Wizard.\n' "$PI_HOST" | tee -a "$REPORT_FILE"
finish
