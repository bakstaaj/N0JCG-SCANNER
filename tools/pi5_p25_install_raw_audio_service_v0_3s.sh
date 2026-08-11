#!/usr/bin/env bash
# Install/restart the raw browser-audio bridge service on the Raspberry Pi.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_DIR=".p25_raw_audio_service_reports"
REPORT_FILE="$REPORT_DIR/install_raw_audio_service_v0_3s_${STAMP}.txt"
UNIT_NAME="pi-p25-browser-audio-raw.service"
UNIT_PATH="/etc/systemd/system/${UNIT_NAME}"
MODE="install"
YES=0
NO_START=0
HTTP_PORT=8072
UDP_PORT=23456
HOST="0.0.0.0"
UDP_HOST="127.0.0.1"
REPO_ROOT="$(pwd -P)"
SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"
UNIT_PREVIEW="$REPORT_DIR/${UNIT_NAME}.${STAMP}.preview"

mkdir -p "$REPORT_DIR" runtime/logs
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
usage() {
  cat <<USAGE
Usage:
  ./tools/pi5_p25_install_raw_audio_service_v0_3s.sh --install --yes
  ./tools/pi5_p25_install_raw_audio_service_v0_3s.sh --install --yes --no-start
  ./tools/pi5_p25_install_raw_audio_service_v0_3s.sh --uninstall --yes

Installs the independent raw browser-audio bridge service on port 8072.
USAGE
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --install) MODE="install"; shift ;;
    --uninstall) MODE="uninstall"; shift ;;
    --yes) YES=1; shift ;;
    --no-start) NO_START=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

sudo_cmd() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
    return $?
  fi
  if sudo -n true >/dev/null 2>&1; then
    sudo "$@"
    return $?
  fi
  if [[ -n "${SUDO_PASSWORD:-}" ]]; then
    printf '%s\n' "$SUDO_PASSWORD" | sudo -S "$@"
    return $?
  fi
  sudo "$@"
}

printf '=== scanner V0.3S raw browser-audio service install ===\n' | tee -a "$REPORT_FILE"
printf 'Mode: %s\n' "$MODE" | tee -a "$REPORT_FILE"
printf 'Repo: %s\n' "$REPO_ROOT" | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "tools" ]]; then
  pass "running from scanner repository root"
else
  fail "run from scanner repository root on the Pi"
fi
if [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]]; then
  pass "Linux host detected"
else
  fail "this installer must run on the Raspberry Pi/Linux runtime"
fi
for cmd in python3 systemctl sudo; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "command available: $cmd"
  else
    fail "missing required command: $cmd"
  fi
done
if [[ -f "tools/pi5_p25_browser_audio_raw_bridge_server.py" ]]; then
  pass "raw audio bridge script exists"
else
  fail "missing tools/pi5_p25_browser_audio_raw_bridge_server.py"
fi
if python3 -m py_compile tools/pi5_p25_browser_audio_raw_bridge_server.py >>"$REPORT_FILE" 2>&1; then
  pass "raw audio bridge script compiles"
else
  fail "raw audio bridge script compile failed"
fi
if [[ "$MODE" == "install" && "$YES" -ne 1 ]]; then
  fail "--install requires --yes"
fi
if [[ "$MODE" == "uninstall" && "$YES" -ne 1 ]]; then
  fail "--uninstall requires --yes"
fi

cat > "$UNIT_PREVIEW" <<UNIT
[Unit]
Description=PI P25 Raw Browser Audio Bridge
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$REPO_ROOT
ExecStart=/usr/bin/python3 $REPO_ROOT/tools/pi5_p25_browser_audio_raw_bridge_server.py --host $HOST --port $HTTP_PORT --udp-host $UDP_HOST --udp-port $UDP_PORT
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
UNIT
pass "wrote unit preview: $UNIT_PREVIEW"

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  finish
fi

if [[ "$MODE" == "install" ]]; then
  sudo_cmd install -m 0644 "$UNIT_PREVIEW" "$UNIT_PATH"
  pass "installed systemd unit: $UNIT_PATH"
  sudo_cmd systemctl daemon-reload
  pass "systemd daemon reloaded"
  sudo_cmd systemctl enable "$UNIT_NAME" >/dev/null
  pass "service enabled for boot: $UNIT_NAME"
  if [[ "$NO_START" -eq 1 ]]; then
    warn "--no-start selected; service was enabled but not restarted"
  else
    sudo_cmd systemctl restart "$UNIT_NAME"
    pass "service restarted: $UNIT_NAME"
  fi
elif [[ "$MODE" == "uninstall" ]]; then
  sudo_cmd systemctl stop "$UNIT_NAME" >/dev/null 2>&1 || true
  sudo_cmd systemctl disable "$UNIT_NAME" >/dev/null 2>&1 || true
  sudo_cmd rm -f "$UNIT_PATH"
  sudo_cmd systemctl daemon-reload
  pass "service stopped/disabled/removed: $UNIT_NAME"
else
  fail "unknown mode: $MODE"
fi

if [[ "$MODE" == "install" && "$NO_START" -ne 1 && "$FAIL_COUNT" -eq 0 ]]; then
  if systemctl is-active --quiet "$UNIT_NAME"; then
    pass "service is active: $UNIT_NAME"
  else
    fail "service is not active after restart: $UNIT_NAME"
  fi
  if systemctl is-enabled --quiet "$UNIT_NAME"; then
    pass "service is enabled: $UNIT_NAME"
  else
    fail "service is not enabled: $UNIT_NAME"
  fi
  python3 - "$HTTP_PORT" <<'PY' >>"$REPORT_FILE" 2>&1 && pass "raw audio bridge status endpoint responded" || warn "raw audio bridge status endpoint did not respond yet"
import json
import sys
import urllib.request
port = int(sys.argv[1])
with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/audio/status", timeout=2) as resp:
    data = json.loads(resp.read().decode("utf-8"))
if not data.get("ok"):
    raise SystemExit(1)
print(json.dumps(data, indent=2, sort_keys=True)[:1200])
PY
fi

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [[ -n "$LAN_IP" ]]; then
  printf 'RAW_AUDIO_URL=http://%s:%s/audio.wav\n' "$LAN_IP" "$HTTP_PORT" | tee -a "$REPORT_FILE"
else
  printf 'RAW_AUDIO_URL=http://<pi-ip>:%s/audio.wav\n' "$HTTP_PORT" | tee -a "$REPORT_FILE"
fi
finish
