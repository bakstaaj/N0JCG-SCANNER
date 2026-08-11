#!/usr/bin/env bash
set -u
PATCH_ID="deploy_v0_5f_desktop_launcher_no_web_autostart"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/${PATCH_ID}_${TS}.txt"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
mkdir -p "$LOG_DIR" 2>/dev/null || true
exec > >(tee -a "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
finish(){
  local rc="$1"
  local win_path
  win_path="$(cygpath -w "$LOG_FILE" 2>/dev/null || printf '%s' "$LOG_FILE")"
  echo "UPLOAD_FILE_MSYS=$LOG_FILE"
  echo "UPLOAD_FILE_WINDOWS=$win_path"
  echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [[ "$rc" -eq 0 && "$FAIL_COUNT" -eq 0 ]]; then echo "FINAL: PASS"; else echo "FINAL: FAIL"; fi
  exit "$rc"
}
trap 'rc=$?; fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; finish "$rc"' ERR

echo "=== Deploy V0.5F desktop launcher and no page-load autostart ==="

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  pass "loaded .env"
else
  warn ".env not found; using shell environment only"
fi
PI_HOST="192.168.254.63"
PI_USER="${PI_USER:-pi}"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"
TARGET="$PI_USER@$PI_HOST"
pass "target fixed to $TARGET:$PI_REPO"

SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
SCP_BASE=(scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
if [[ -n "${PI_PASSWORD:-}" ]]; then
  export SSHPASS="$PI_PASSWORD"
fi
if [[ -n "${SSHPASS:-}" ]]; then
  SSH_CMD=(sshpass -e "${SSH_BASE[@]}")
  SCP_CMD=(sshpass -e "${SCP_BASE[@]}")
  pass "using sshpass with PI_PASSWORD/SSHPASS"
else
  SSH_CMD=("${SSH_BASE[@]}" -o BatchMode=yes)
  SCP_CMD=("${SCP_BASE[@]}" -o BatchMode=yes)
  warn "no PI_PASSWORD/SSHPASS found; trying SSH key auth only"
fi

"${SSH_CMD[@]}" "$TARGET" "test -d '$PI_REPO' && test -w '$PI_REPO'"
pass "Pi repo reachable without interactive prompt"

if [[ ! -f web/app.js || ! -f web/index.html ]]; then
  fail "local web/app.js or web/index.html missing; run V0.5F patch first"
  finish 1
fi
if ! grep -q 'V0.5F_DESKTOP_LAUNCHER_NO_PAGE_AUTOSTART' web/app.js; then
  fail "local web/app.js does not contain V0.5F marker; run V0.5F patch first"
  finish 1
fi
pass "local V0.5F UI marker present"

TAR="/tmp/pi_p25_v0_5f_ui_${TS}.tar"
tar -cf "$TAR" web/app.js web/index.html
pass "built UI deploy tar"
"${SCP_CMD[@]}" "$TAR" "$TARGET:/tmp/pi_p25_v0_5f_ui.tar"
pass "copied UI deploy tar to Pi"
rm -f "$TAR"

"${SSH_CMD[@]}" "$TARGET" "PI_REPO='$PI_REPO' bash -s" <<'REMOTE'
set -euo pipefail
cd "$PI_REPO"
mkdir -p "runtime/patch_backups/v0_5f_$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="runtime/patch_backups/v0_5f_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
cp -p web/app.js "$backup_dir/app.js.before" 2>/dev/null || true
cp -p web/index.html "$backup_dir/index.html.before" 2>/dev/null || true
tar -xf /tmp/pi_p25_v0_5f_ui.tar -C "$PI_REPO"
python3 - <<'PYREMOTE'
from pathlib import Path
for p in [Path('web/app.js'), Path('web/index.html')]:
    text = p.read_text(encoding='utf-8')
    text = '\n'.join(line.rstrip() for line in text.splitlines()).rstrip() + '\n'
    p.write_text(text, encoding='utf-8')
PYREMOTE
python3 -m py_compile src/pi_p25_scanner/backend.py
mkdir -p tools /home/pi/Desktop
cat > tools/start_p25_scanner_desktop.sh <<'STARTER'
#!/usr/bin/env bash
set -euo pipefail
URL="http://127.0.0.1:8070/"
STATUS="http://127.0.0.1:8070/api/status"
LOG_DIR="$HOME/scanner/runtime/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/desktop_launcher.log"
{
  echo "===== $(date -Is) P25 desktop launcher ====="
  if ! curl -fsS --max-time 3 "$STATUS" >/dev/null; then
    echo "Backend status endpoint is not reachable at $STATUS"
    echo "Open the scanner service/backend first, then retry this launcher."
  else
    echo "Backend reachable; opening the stopped dashboard."
  fi
  if command -v chromium-browser >/dev/null 2>&1; then
    nohup chromium-browser --new-window "$URL" >/dev/null 2>&1 &
  elif command -v chromium >/dev/null 2>&1; then
    nohup chromium --new-window "$URL" >/dev/null 2>&1 &
  elif command -v x-www-browser >/dev/null 2>&1; then
    nohup x-www-browser "$URL" >/dev/null 2>&1 &
  elif command -v xdg-open >/dev/null 2>&1; then
    nohup xdg-open "$URL" >/dev/null 2>&1 &
  else
    echo "No browser launcher found. Open $URL manually."
  fi
} >>"$LOG" 2>&1
STARTER
chmod +x tools/start_p25_scanner_desktop.sh
cat > /home/pi/Desktop/P25-Scanner.desktop <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=P25 Scanner
Comment=Open PI Scanner; use Start Scanning and Audio to begin reception
Exec=/home/pi/n0jcg-scanner/tools/start_p25_scanner_desktop.sh
Path=/home/pi/n0jcg-scanner
Icon=network-wireless
Terminal=false
Categories=Network;HamRadio;
StartupNotify=true
DESKTOP
chmod +x /home/pi/Desktop/P25-Scanner.desktop
if command -v gio >/dev/null 2>&1; then
  gio set /home/pi/Desktop/P25-Scanner.desktop metadata::trusted true >/dev/null 2>&1 || true
fi
# Restart backend safely if it is running from this repo. Avoid matching the pkill command itself.
pids=$(pgrep -f '[p]i_p25_scanner.backend' || true)
if [[ -n "$pids" ]]; then
  kill $pids || true
  sleep 1
fi
nohup python3 -m pi_p25_scanner.backend --host 0.0.0.0 --port 8070 > runtime/logs/backend_v0_5f.log 2>&1 &
sleep 2
REMOTE
pass "deployed UI and created /home/pi/Desktop/P25-Scanner.desktop"

APP_HTTP="$("${SSH_CMD[@]}" "$TARGET" "curl -fsS --max-time 5 http://127.0.0.1:8070/app.js | grep -c 'V0.5F_DESKTOP_LAUNCHER_NO_PAGE_AUTOSTART' || true")"
if [[ "$APP_HTTP" == "1" ]]; then
  pass "verified V0.5F marker is served by /app.js"
else
  fail "V0.5F marker was not served by /app.js"
  finish 1
fi
IDX_HTTP="$("${SSH_CMD[@]}" "$TARGET" "curl -fsS --max-time 5 http://127.0.0.1:8070/index.html | grep -c '0.5f-desktop-launcher' || true")"
if [[ "$IDX_HTTP" == "1" ]]; then
  pass "verified cache-busted app.js reference is served by /index.html"
else
  fail "cache-busted app.js reference was not served by /index.html"
  finish 1
fi
"${SSH_CMD[@]}" "$TARGET" "test -x /home/pi/Desktop/P25-Scanner.desktop && test -x '$PI_REPO/tools/start_p25_scanner_desktop.sh'"
pass "verified desktop launcher files are executable"
"${SSH_CMD[@]}" "$TARGET" "curl -fsS --max-time 5 http://127.0.0.1:8070/api/status >/dev/null"
pass "backend status endpoint responded after deploy"
finish 0
