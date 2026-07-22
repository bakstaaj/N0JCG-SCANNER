#!/usr/bin/env bash
# Guarded systemd service installer for the PI-P25-SCANNER backend.
# Default mode is dry-run. Use --install --yes on the Raspberry Pi to install/start.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_backend_service_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/backend_service_install_${STAMP}.txt"
UNIT_NAME="pi-p25-scanner.service"
UNIT_PATH="/etc/systemd/system/$UNIT_NAME"
MODE="dry-run"
YES=0
NO_START=0
BACKEND_PORT=8070
HOST="0.0.0.0"
REPO_ROOT="$(pwd -P)"
SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"
UNIT_PREVIEW="$REPORT_DIR/${UNIT_NAME}.${STAMP}.preview"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }
usage() {
  cat <<USAGE
Usage:
  ./tools/pi5_p25_backend_service_install.sh --dry-run
  ./tools/pi5_p25_backend_service_install.sh --install --yes
  ./tools/pi5_p25_backend_service_install.sh --install --yes --no-start
  ./tools/pi5_p25_backend_service_install.sh --uninstall --yes

Installs a systemd service for the PI-P25-SCANNER backend on port 8070.
The scanner/OP25 start action still uses the validated runtime marker:
  runtime/settings/op25_validated_rx_command.env
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      MODE="dry-run"
      shift
      ;;
    --install)
      MODE="install"
      shift
      ;;
    --uninstall)
      MODE="uninstall"
      shift
      ;;
    --yes)
      YES=1
      shift
      ;;
    --no-start)
      NO_START=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage
      exit 1
      ;;
  esac
done

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"
printf '=== PI-P25-SCANNER backend systemd service installer ===\n' | tee -a "$REPORT_FILE"
printf 'Mode: %s\n' "$MODE" | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "web" ]]; then
  pass "running from repository root: $REPO_ROOT"
else
  fail "run from PI-P25-SCANNER repository root"
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

if [[ -f "runtime/settings/op25_validated_rx_command.env" ]]; then
  pass "validated OP25 command marker exists"
else
  warn "validated OP25 marker missing; backend service can run, but UI Start will stay gated until live command probe passes"
fi

if [[ -d "src/pi_p25_scanner" && -f "src/pi_p25_scanner/backend.py" ]]; then
  pass "backend module exists"
else
  fail "backend module missing"
fi

cat > "$UNIT_PREVIEW" <<UNIT
[Unit]
Description=PI P25 Scanner Backend
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$REPO_ROOT
Environment=PYTHONPATH=$REPO_ROOT/src
ExecStart=/usr/bin/python3 -m pi_p25_scanner.backend --host $HOST --port $BACKEND_PORT
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
  printf '\nDry-run only. To install on the Pi, run:\n' | tee -a "$REPORT_FILE"
  printf './tools/pi5_p25_backend_service_install.sh --install --yes\n' | tee -a "$REPORT_FILE"
elif [[ "$MODE" == "install" ]]; then
  if [[ "$YES" -ne 1 ]]; then
    fail "--install requires --yes"
  fi
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    sudo install -m 0644 "$UNIT_PREVIEW" "$UNIT_PATH"
    pass "installed systemd unit: $UNIT_PATH"
    sudo systemctl daemon-reload
    pass "systemd daemon reloaded"
    sudo systemctl enable "$UNIT_NAME" >/dev/null
    pass "service enabled for boot: $UNIT_NAME"
    if [[ "$NO_START" -eq 1 ]]; then
      warn "--no-start selected; service was enabled but not started"
    else
      sudo systemctl restart "$UNIT_NAME"
      pass "service restarted: $UNIT_NAME"
    fi
  fi
elif [[ "$MODE" == "uninstall" ]]; then
  if [[ "$YES" -ne 1 ]]; then
    fail "--uninstall requires --yes"
  fi
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    sudo systemctl stop "$UNIT_NAME" >/dev/null 2>&1 || true
    sudo systemctl disable "$UNIT_NAME" >/dev/null 2>&1 || true
    sudo rm -f "$UNIT_PATH"
    sudo systemctl daemon-reload
    pass "service stopped/disabled/removed: $UNIT_NAME"
  fi
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
fi

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [[ -n "$LAN_IP" ]]; then
  printf 'URL: http://%s:%s\n' "$LAN_IP" "$BACKEND_PORT" | tee -a "$REPORT_FILE"
else
  printf 'URL: http://<pi-ip>:%s\n' "$BACKEND_PORT" | tee -a "$REPORT_FILE"
fi

printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
