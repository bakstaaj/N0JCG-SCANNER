#!/usr/bin/env bash
# Validate the installed PI-P25-SCANNER backend systemd service and port 8070 UI.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_backend_service_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/backend_service_probe_${STAMP}.txt"
CLIENT_LOG="$REPORT_DIR/backend_service_client_${STAMP}.log"
UNIT_NAME="pi-p25-scanner.service"
UNIT_PATH="/etc/systemd/system/$UNIT_NAME"
PORT=8070

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"
: > "$CLIENT_LOG"
printf '=== PI-P25-SCANNER backend service probe ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "web" ]]; then
  pass "running from repository root"
else
  fail "run from PI-P25-SCANNER repository root"
fi

for cmd in python3 systemctl; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "command available: $cmd"
  else
    fail "missing required command: $cmd"
  fi
done

if [[ -f "$UNIT_PATH" ]]; then
  pass "systemd unit exists: $UNIT_PATH"
else
  fail "systemd unit missing; run ./tools/pi5_p25_backend_service_install.sh --install --yes"
fi

if systemctl is-enabled --quiet "$UNIT_NAME"; then
  pass "service is enabled: $UNIT_NAME"
else
  fail "service is not enabled: $UNIT_NAME"
fi

if systemctl is-active --quiet "$UNIT_NAME"; then
  pass "service is active: $UNIT_NAME"
else
  fail "service is not active: $UNIT_NAME"
fi

if [[ -f "runtime/settings/op25_validated_rx_command.env" ]]; then
  pass "validated OP25 command marker exists"
else
  warn "validated OP25 marker missing; backend should run, but UI scanner Start remains gated"
fi

if PYTHONPATH=src python3 - "$PORT" "$CLIENT_LOG" <<'PY_CLIENT'
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

port = int(sys.argv[1])
client_log = Path(sys.argv[2])
base = f"http://127.0.0.1:{port}"


def write(message: str) -> None:
    with client_log.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def get(path: str) -> tuple[int, str]:
    with urllib.request.urlopen(base + path, timeout=5) as response:
        body = response.read().decode("utf-8")
        return response.status, body

last_error: Exception | None = None
for _ in range(40):
    try:
        status_code, body = get("/api/status")
        payload = json.loads(body)
        write(f"GET /api/status -> {status_code} {json.dumps(payload, sort_keys=True)[:2000]}")
        if status_code == 200 and payload.get("scanner_state"):
            break
    except Exception as exc:  # noqa: BLE001 - probe diagnostic
        last_error = exc
        time.sleep(0.25)
else:
    raise SystemExit(f"backend did not answer on port {port}: {last_error}")

status_code, body = get("/")
write(f"GET / -> {status_code} bytes={len(body)}")
if status_code != 200:
    raise SystemExit(f"root UI returned HTTP {status_code}")
if "port <strong>8070</strong>" not in body:
    raise SystemExit("root UI did not advertise standard port 8070")
print("BACKEND_SERVICE_PROBE_PASS")
PY_CLIENT
then
  pass "backend service answered on port 8070 and served UI"
else
  fail "backend service HTTP probe failed; see $CLIENT_LOG"
fi

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [[ -n "$LAN_IP" ]]; then
  printf 'URL: http://%s:%s\n' "$LAN_IP" "$PORT" | tee -a "$REPORT_FILE"
else
  printf 'URL: http://<pi-ip>:%s\n' "$PORT" | tee -a "$REPORT_FILE"
fi
printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'Client log: %s\n' "$CLIENT_LOG" | tee -a "$REPORT_FILE"
printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
