#!/usr/bin/env bash
# Bounded UI live-control/static validation for PI-P25-SCANNER V0.2C.
# Runs the backend on loopback and verifies the web UI exposes live-control fields.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_ui_live_control_probe_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/ui_live_control_${STAMP}.txt"
BACKEND_LOG="$REPORT_DIR/backend_${STAMP}.log"
CLIENT_LOG="$REPORT_DIR/client_${STAMP}.log"
PORT="${P25_UI_LIVE_TEST_PORT:-18097}"
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
printf '=== PI-P25-SCANNER UI live-control probe ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "web" ]]; then
  pass "running from repository root"
else
  fail "run from PI-P25-SCANNER repository root"
fi

for cmd in python3 timeout; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "command available: $cmd"
  else
    fail "missing required command: $cmd"
  fi
done

if [[ -f "web/index.html" && -f "web/app.js" && -f "web/app.css" ]]; then
  pass "web assets exist"
else
  fail "missing web assets"
fi

if python3 - "$PORT" <<'PY_PORT'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket()
try:
    sock.bind(("127.0.0.1", port))
finally:
    sock.close()
PY_PORT
then
  pass "loopback UI test port available: $PORT"
else
  fail "loopback UI test port unavailable: $PORT"
fi

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

PYTHONPATH=src python3 -m pi_p25_scanner.backend --host 127.0.0.1 --port "$PORT" > "$BACKEND_LOG" 2>&1 &
BACKEND_PID="$!"
pass "backend started on loopback: pid=$BACKEND_PID port=$PORT"

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
    with urllib.request.urlopen(base + path, timeout=10) as response:
        body = response.read().decode("utf-8")
        return response.status, body

last_error: Exception | None = None
for _ in range(40):
    try:
        status_code, body = get("/api/status")
        payload = json.loads(body)
        if status_code == 200 and payload.get("scanner_state"):
            write(f"GET /api/status -> {status_code} {json.dumps(payload, sort_keys=True)[:2000]}")
            break
    except Exception as exc:  # noqa: BLE001 - probe diagnostic
        last_error = exc
        time.sleep(0.25)
else:
    raise SystemExit(f"backend status endpoint never became ready: {last_error}")

checks = {
    "/": ["port <strong>8070</strong>", "validatedMarkerState", "logTail", "Validated OP25 Launch", "Backend / OP25"],
    "/app.js": ["validated_marker", "setButtonsForState", "scanner_state"],
    "/app.css": ["badge-ok", "two-column", "controls-panel"],
}
for path, required in checks.items():
    status_code, body = get(path)
    write(f"GET {path} -> {status_code} bytes={len(body)}")
    if status_code != 200:
        raise SystemExit(f"{path} returned HTTP {status_code}")
    for needle in required:
        if needle not in body:
            raise SystemExit(f"{path} missing expected UI marker: {needle}")

print("UI_LIVE_CONTROL_PROBE_PASS")
PY_CLIENT
then
  pass "UI live-control static/status probe passed"
else
  fail "UI live-control probe failed; see $CLIENT_LOG and $BACKEND_LOG"
fi

if [[ -s "$BACKEND_LOG" ]]; then
  pass "backend log captured: $BACKEND_LOG"
else
  warn "backend log is empty: $BACKEND_LOG"
fi

printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'Backend log: %s\n' "$BACKEND_LOG" | tee -a "$REPORT_FILE"
printf 'Client log: %s\n' "$CLIENT_LOG" | tee -a "$REPORT_FILE"
printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
