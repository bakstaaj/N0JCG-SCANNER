#!/usr/bin/env bash
# Install/uninstall the independent raw browser-audio bridge systemd service.
# Run on the Raspberry Pi from the scanner repository root.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
MODE="dry-run"
YES=0
NO_START=0
SERVICE_NAME="pi-p25-raw-audio-bridge.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
HTTP_HOST="0.0.0.0"
HTTP_PORT="${P25_SCANNER_AUDIO_BRIDGE_PORT:-8072}"
UDP_HOST="127.0.0.1"
UDP_PORT="${P25_SCANNER_AUDIO_UDP_PORT:-23456}"
REPO_ROOT="$(pwd -P)"
SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"
REPORT_DIR=".p25_raw_audio_bridge_service_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="${REPORT_DIR}/raw_audio_bridge_service_${STAMP}.txt"
UNIT_PREVIEW="${REPORT_DIR}/${SERVICE_NAME}.${STAMP}.preview"
BRIDGE_SCRIPT="${REPO_ROOT}/tools/pi5_p25_browser_audio_raw_bridge_server.py"

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
  ./tools/pi5_p25_raw_audio_bridge_service_install.sh --dry-run
  ./tools/pi5_p25_raw_audio_bridge_service_install.sh --install --yes
  ./tools/pi5_p25_raw_audio_bridge_service_install.sh --install --yes --no-start
  ./tools/pi5_p25_raw_audio_bridge_service_install.sh --uninstall --yes

Installs an independent raw OP25 UDP PCM browser-audio bridge:
  HTTP stream: http://<pi-ip>:${HTTP_PORT}/audio.wav
  OP25 UDP:    ${UDP_HOST}:${UDP_PORT}
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) MODE="dry-run"; shift ;;
    --install) MODE="install"; shift ;;
    --uninstall) MODE="uninstall"; shift ;;
    --yes) YES=1; shift ;;
    --no-start) NO_START=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage; exit 2 ;;
  esac
done

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"
printf '=== scanner raw audio bridge service installer ===\n' | tee -a "$REPORT_FILE"
printf 'Mode: %s\n' "$MODE" | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "tools" ]]; then
  pass "running from repository root: $REPO_ROOT"
else
  fail "run from scanner repository root"
fi
if [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]]; then
  pass "Linux host detected"
else
  fail "service install target must be the Raspberry Pi/Linux runtime"
fi
for cmd in python3 systemctl sudo; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "command available: $cmd"
  else
    fail "missing required command: $cmd"
  fi
done
if [[ -f "$BRIDGE_SCRIPT" ]]; then
  pass "raw bridge script exists: $BRIDGE_SCRIPT"
else
  fail "missing raw bridge script: $BRIDGE_SCRIPT"
fi
if [[ -f "$BRIDGE_SCRIPT" ]] && python3 -m py_compile "$BRIDGE_SCRIPT" >>"$REPORT_FILE" 2>&1; then
  pass "raw bridge script compiles"
elif [[ -f "$BRIDGE_SCRIPT" ]]; then
  fail "raw bridge script compile failed"
fi

cat > "$UNIT_PREVIEW" <<UNIT
[Unit]
Description=PI P25 Raw Browser Audio Bridge
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${REPO_ROOT}
ExecStart=/usr/bin/python3 ${BRIDGE_SCRIPT} --host ${HTTP_HOST} --port ${HTTP_PORT} --udp-host ${UDP_HOST} --udp-port ${UDP_PORT}
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
UNIT
pass "wrote unit preview: $UNIT_PREVIEW"

if [[ "$MODE" == "dry-run" ]]; then
  printf '\nUnit preview:\n' | tee -a "$REPORT_FILE"
  cat "$UNIT_PREVIEW" | tee -a "$REPORT_FILE"
elif [[ "$MODE" == "install" ]]; then
  if [[ "$YES" -ne 1 ]]; then
    fail "--install requires --yes"
  fi
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    sudo install -m 0644 "$UNIT_PREVIEW" "$SERVICE_PATH"
    pass "installed systemd unit: $SERVICE_PATH"
    sudo systemctl daemon-reload
    pass "systemd daemon reloaded"
    sudo systemctl enable "$SERVICE_NAME" >/dev/null
    pass "service enabled for boot: $SERVICE_NAME"
    if [[ "$NO_START" -eq 1 ]]; then
      warn "--no-start selected; service was enabled but not started"
    else
      sudo systemctl restart "$SERVICE_NAME"
      pass "service restarted: $SERVICE_NAME"
    fi
  fi
elif [[ "$MODE" == "uninstall" ]]; then
  if [[ "$YES" -ne 1 ]]; then
    fail "--uninstall requires --yes"
  fi
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    sudo systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
    sudo systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 || true
    sudo rm -f "$SERVICE_PATH"
    sudo systemctl daemon-reload
    pass "service stopped/disabled/removed: $SERVICE_NAME"
  fi
else
  fail "unknown mode: $MODE"
fi

if [[ "$MODE" == "install" && "$NO_START" -ne 1 && "$FAIL_COUNT" -eq 0 ]]; then
  if systemctl is-active --quiet "$SERVICE_NAME"; then
    pass "service is active: $SERVICE_NAME"
  else
    fail "service is not active after restart: $SERVICE_NAME"
  fi
  python3 - "$HTTP_PORT" >>"$REPORT_FILE" 2>&1 <<'PY_PROBE'
import json
import sys
import urllib.request
port = int(sys.argv[1])
with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/audio/status', timeout=3) as response:
    payload = json.loads(response.read().decode('utf-8'))
print('AUDIO_STATUS_OK=' + str(bool(payload.get('ok'))))
print('AUDIO_STATUS_MODE=' + str(payload.get('mode')))
PY_PROBE
  if [[ "$?" -eq 0 ]]; then
    pass "audio bridge local status probe passed"
  else
    fail "audio bridge local status probe failed"
  fi
fi

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [[ -n "$LAN_IP" ]]; then
  printf 'AUDIO_URL: http://%s:%s/audio.wav\n' "$LAN_IP" "$HTTP_PORT" | tee -a "$REPORT_FILE"
else
  printf 'AUDIO_URL: http://<pi-ip>:%s/audio.wav\n' "$HTTP_PORT" | tee -a "$REPORT_FILE"
fi
finish
