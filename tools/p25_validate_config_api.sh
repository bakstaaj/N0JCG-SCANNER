#!/usr/bin/env bash
# Validate the PI-P25-SCANNER config API/UI backend endpoints without live decoder launch.
# Run from the PI-P25-SCANNER repository root.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_config_api_reports"
REPORT_FILE="$REPORT_DIR/config_api_$(date -u +%Y%m%dT%H%M%SZ).txt"
BACKEND_LOG="$REPORT_DIR/backend_$(date -u +%Y%m%dT%H%M%SZ).log"
PORT="${P25_CONFIG_API_TEST_PORT:-18090}"
BACKEND_PID=""

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

cleanup() {
  if [[ -n "$BACKEND_PID" ]]; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
    wait "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"
printf '=== PI-P25-SCANNER config API validation ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "web" ]]; then
  pass "running from repository root"
else
  fail "run from PI-P25-SCANNER repository root"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
fi

if python3 - "$PORT" <<'PY'
import socket
import sys
port = int(sys.argv[1])
sock = socket.socket()
try:
    sock.bind(("127.0.0.1", port))
finally:
    sock.close()
PY
then
  pass "loopback test port available: $PORT"
else
  fail "loopback test port unavailable: $PORT"
fi

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

PYTHONPATH=src P25_SCANNER_OP25_OUTPUT=runtime/op25_config_api_probe \
  python3 src/pi_p25_scanner/backend.py --host 127.0.0.1 --port "$PORT" >"$BACKEND_LOG" 2>&1 &
BACKEND_PID="$!"
pass "backend started for API validation: pid=$BACKEND_PID"

if PYTHONPATH=src python3 - "$PORT" <<'PY'
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

port = int(sys.argv[1])
base = f"http://127.0.0.1:{port}"


def request(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        text = response.read().decode("utf-8")
    return json.loads(text) if text else {}

last_error = None
for _ in range(40):
    try:
        status = request("/api/status")
        if status.get("scanner_state"):
            break
    except Exception as exc:  # noqa: BLE001 - validator needs diagnostic text
        last_error = exc
        time.sleep(0.25)
else:
    raise SystemExit(f"status endpoint never became ready: {last_error}")

config = request("/api/config")
if not config.get("ok") or not isinstance(config.get("config"), dict):
    raise SystemExit("/api/config did not return an editable config")

init_result = request("/api/config/init-local", "POST")
if not init_result.get("ok"):
    raise SystemExit("/api/config/init-local failed")

config = request("/api/config")
payload = config.get("config")
if not isinstance(payload, dict):
    raise SystemExit("config payload missing after local init")

save_result = request("/api/config/save", "POST", {"config": payload})
if not save_result.get("ok"):
    raise SystemExit("/api/config/save failed")

op25_result = request("/api/decoder/generate-config", "POST")
if not op25_result.get("ok"):
    raise SystemExit("/api/decoder/generate-config failed")

status = request("/api/status")
if "config" not in status:
    raise SystemExit("/api/status missing config metadata")

print("CONFIG_API_SMOKE_PASS")
PY
then
  pass "config API smoke validation passed"
else
  fail "config API smoke validation failed; see $BACKEND_LOG"
fi

if [[ -s "$BACKEND_LOG" ]]; then
  pass "backend validation log captured: $BACKEND_LOG"
else
  warn "backend validation log is empty: $BACKEND_LOG"
fi

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
printf 'Report: %s\n' "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
