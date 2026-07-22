#!/usr/bin/env bash
# Bounded backend live-launch validation for PI-P25-SCANNER.
# Runs the backend on loopback, calls /api/scanner/start, verifies OP25 is running,
# then calls /api/scanner/stop. Requires a validated OP25 marker.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_backend_live_launch_probe_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/backend_live_launch_${STAMP}.txt"
BACKEND_LOG="$REPORT_DIR/backend_${STAMP}.log"
CLIENT_LOG="$REPORT_DIR/client_${STAMP}.log"
PORT="${P25_BACKEND_LIVE_TEST_PORT:-18094}"
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
printf '=== PI-P25-SCANNER backend live-launch probe ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "tools" ]]; then
  pass "running from repository root"
else
  fail "run from PI-P25-SCANNER repository root"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if [[ -f "runtime/settings/op25_validated_rx_command.env" ]]; then
  pass "validated OP25 command marker exists"
else
  fail "validated OP25 command marker missing; run tools/pi5_p25_op25_live_command_probe.sh --rx-smoke --seconds 20 --yes first"
fi

for cmd in python3 timeout; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "command available: $cmd"
  else
    fail "missing required command: $cmd"
  fi
done

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
  pass "loopback test port available: $PORT"
else
  fail "loopback test port unavailable: $PORT"
fi

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

PYTHONPATH=src python3 src/pi_p25_scanner/backend.py --host 127.0.0.1 --port "$PORT" > "$BACKEND_LOG" 2>&1 &
BACKEND_PID="$!"
pass "backend started: pid=$BACKEND_PID"

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


def request(path: str, method: str = "GET") -> dict:
    req = urllib.request.Request(base + path, method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
        body = response.read().decode("utf-8")
    payload = json.loads(body) if body else {}
    write(f"{method} {path} -> {response.status} {json.dumps(payload, sort_keys=True)[:2000]}")
    return payload


last_error: Exception | None = None
for _ in range(40):
    try:
        status = request("/api/status")
        if status.get("scanner_state"):
            break
    except Exception as exc:  # noqa: BLE001 - probe diagnostic
        last_error = exc
        time.sleep(0.25)
else:
    raise SystemExit(f"backend status endpoint never became ready: {last_error}")

start = request("/api/scanner/start", "POST")
if start.get("scanner_state") != "running":
    raise SystemExit(f"start did not enter running state: {start.get('scanner_state')}")
process = start.get("decoder_process", {})
if process.get("command_source") != "validated_marker":
    raise SystemExit(f"backend did not use validated marker: {process.get('command_source')}")
if not process.get("running") or not process.get("pid"):
    raise SystemExit(f"decoder process not reported running: {process}")

time.sleep(8)
status = request("/api/status")
process = status.get("decoder_process", {})
if status.get("scanner_state") != "running" or not process.get("running"):
    raise SystemExit(f"decoder did not remain running long enough: {status.get('scanner_state')} {process}")

stop = request("/api/scanner/stop", "POST")
if stop.get("scanner_state") != "stopped":
    raise SystemExit(f"stop did not report stopped: {stop.get('scanner_state')}")

print("BACKEND_LIVE_LAUNCH_PROBE_PASS")
PY_CLIENT
then
  pass "backend start/status/stop live-launch probe passed"
else
  fail "backend live-launch client failed; see $CLIENT_LOG and $BACKEND_LOG"
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
