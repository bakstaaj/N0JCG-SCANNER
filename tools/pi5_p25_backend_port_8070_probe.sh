#!/usr/bin/env bash
set -Eeuo pipefail

PORT="8070"
HOST="127.0.0.1"
REPORT_DIR=".p25_backend_port_8070_probe_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/backend_port_8070_${STAMP}.txt"
BACKEND_LOG="$REPORT_DIR/backend_${STAMP}.log"
CLIENT_LOG="$REPORT_DIR/client_${STAMP}.log"
PID=""

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"
: > "$BACKEND_LOG"
: > "$CLIENT_LOG"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); }

cleanup() {
  if [[ -n "$PID" ]] && kill -0 "$PID" >/dev/null 2>&1; then
    kill "$PID" >/dev/null 2>&1 || true
    wait "$PID" >/dev/null 2>&1 || true
  fi
}
finish() {
  cleanup
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
}
trap finish EXIT

printf '=== PI-P25-SCANNER backend default port 8070 probe ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "web" ]]; then
  pass "running from repository root"
else
  fail "run from PI-P25-SCANNER repository root"
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 is required"
  exit 1
fi

if python3 - "$HOST" "$PORT" <<'PY_PORT'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
PY_PORT
then
  pass "default port available: ${HOST}:${PORT}"
else
  fail "default port is already in use: ${HOST}:${PORT}"
  exit 1
fi

PYTHONPATH=src python3 -m pi_p25_scanner.backend --host "$HOST" > "$BACKEND_LOG" 2>&1 &
PID="$!"
pass "backend started with default port: pid=$PID"

python3 - "$HOST" "$PORT" "$CLIENT_LOG" <<'PY_CLIENT'
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

host, port, log_path = sys.argv[1], sys.argv[2], Path(sys.argv[3])
base = f"http://{host}:{port}"
deadline = time.time() + 12
last_error = ""
while time.time() < deadline:
    try:
        with urllib.request.urlopen(base + "/api/status", timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        log_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if payload.get("decoder_engine") != "op25":
            raise SystemExit("unexpected decoder_engine in status payload")
        print("BACKEND_DEFAULT_PORT_8070_PROBE_PASS")
        raise SystemExit(0)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        last_error = repr(exc)
        time.sleep(0.5)
log_path.write_text(f"ERROR: backend did not respond on {base}: {last_error}\n", encoding="utf-8")
raise SystemExit(1)
PY_CLIENT
client_rc=$?
if [[ "$client_rc" -eq 0 ]]; then
  pass "backend responded on default port ${HOST}:${PORT}"
else
  fail "backend did not respond on default port ${HOST}:${PORT}; see $CLIENT_LOG and $BACKEND_LOG"
fi

if [[ -n "$PID" ]] && kill -0 "$PID" >/dev/null 2>&1; then
  kill "$PID" >/dev/null 2>&1 || true
  wait "$PID" >/dev/null 2>&1 || true
  PID=""
  pass "backend stopped"
else
  warn "backend process was not running at cleanup"
fi
