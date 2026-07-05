#!/usr/bin/env bash
# Validate PI-P25-SCANNER config API/UI backend endpoints without live decoder launch.
# This smoke test preserves and restores the operator's runtime config.
# Run from the PI-P25-SCANNER repository root.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_config_api_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/config_api_${STAMP}.txt"
BACKEND_LOG="$REPORT_DIR/backend_${STAMP}.log"
CLIENT_LOG="$REPORT_DIR/client_${STAMP}.log"
RUNTIME_CONFIG="runtime/settings/p25_systems.json"
RUNTIME_BACKUP="$REPORT_DIR/runtime_config_before_${STAMP}.json"
RUNTIME_EXISTED=0
BACKEND_PID=""
PORT="${P25_CONFIG_API_TEST_PORT:-}"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

restore_runtime_config() {
  if [[ "$RUNTIME_EXISTED" -eq 1 && -f "$RUNTIME_BACKUP" ]]; then
    mkdir -p "$(dirname "$RUNTIME_CONFIG")"
    cp "$RUNTIME_BACKUP" "$RUNTIME_CONFIG"
  elif [[ "$RUNTIME_EXISTED" -eq 0 ]]; then
    rm -f "$RUNTIME_CONFIG"
  fi
}

cleanup() {
  if [[ -n "$BACKEND_PID" ]]; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
    wait "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
  restore_runtime_config
}
trap cleanup EXIT

find_free_port() {
  python3 - <<'PY'
import socket
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
}

printf '=== PI-P25-SCANNER config API validation ===\n'
mkdir -p "$REPORT_DIR" runtime/settings
: > "$REPORT_FILE"
: > "$BACKEND_LOG"
: > "$CLIENT_LOG"
printf '=== PI-P25-SCANNER config API validation ===\n' >> "$REPORT_FILE"

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

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if [[ -f "$RUNTIME_CONFIG" ]]; then
  cp "$RUNTIME_CONFIG" "$RUNTIME_BACKUP"
  RUNTIME_EXISTED=1
  pass "runtime config backed up for smoke test restore"
else
  RUNTIME_EXISTED=0
  pass "no runtime config existed before smoke test"
fi

# Force the smoke test to start from a known-good template, then restore the operator's file at exit.
if [[ -f "config/p25_systems.local.example.json" ]]; then
  cp "config/p25_systems.local.example.json" "$RUNTIME_CONFIG"
  pass "seeded smoke-test runtime config from local template"
elif [[ -f "config/p25_systems.example.json" ]]; then
  cp "config/p25_systems.example.json" "$RUNTIME_CONFIG"
  pass "seeded smoke-test runtime config from source example"
else
  fail "no config template available for smoke test"
fi

if [[ -z "$PORT" ]]; then
  PORT="$(find_free_port)"
  pass "selected dynamic loopback test port: $PORT"
else
  pass "using requested loopback test port: $PORT"
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

PYTHONPATH=src \
P25_SCANNER_CONFIG="$RUNTIME_CONFIG" \
P25_SCANNER_OP25_OUTPUT="runtime/op25_config_api_probe" \
  python3 src/pi_p25_scanner/backend.py --host 127.0.0.1 --port "$PORT" >"$BACKEND_LOG" 2>&1 &
BACKEND_PID="$!"
pass "backend started for API validation: pid=$BACKEND_PID"

sleep 0.2
if kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
  pass "backend process still running after startup grace period"
else
  fail "backend exited during startup; see $BACKEND_LOG"
fi

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if PYTHONPATH=src python3 - "$PORT" >"$CLIENT_LOG" 2>&1 <<'PY'
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
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            text = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise RuntimeError(f"{method} {path} failed: {exc}") from exc
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {path} returned invalid JSON status={status}: {text[:500]}") from exc

last_error = None
for attempt in range(60):
    try:
        status = request("/api/status")
        if status.get("scanner_state"):
            print(f"READY_AFTER_ATTEMPTS={attempt + 1}")
            break
    except Exception as exc:  # validator needs complete diagnostic text
        last_error = exc
        time.sleep(0.25)
else:
    raise SystemExit(f"status endpoint never became ready: {last_error}")

config = request("/api/config")
if not config.get("ok") or not isinstance(config.get("config"), dict):
    raise SystemExit(f"/api/config did not return editable config: {json.dumps(config, sort_keys=True)[:1000]}")

init_result = request("/api/config/init-local", "POST")
if not init_result.get("ok"):
    raise SystemExit(f"/api/config/init-local failed: {json.dumps(init_result, sort_keys=True)[:1000]}")

config = request("/api/config")
payload = config.get("config")
if not isinstance(payload, dict):
    raise SystemExit(f"config payload missing after local init: {json.dumps(config, sort_keys=True)[:1000]}")

save_result = request("/api/config/save", "POST", {"config": payload})
if not save_result.get("ok"):
    raise SystemExit(f"/api/config/save failed: {json.dumps(save_result, sort_keys=True)[:1000]}")

op25_result = request("/api/decoder/generate-config", "POST")
if not op25_result.get("ok"):
    raise SystemExit(f"/api/decoder/generate-config failed: {json.dumps(op25_result, sort_keys=True)[:1000]}")

status = request("/api/status")
if "config" not in status:
    raise SystemExit(f"/api/status missing config metadata: {json.dumps(status, sort_keys=True)[:1000]}")

print("CONFIG_API_SMOKE_PASS")
PY
  then
    pass "config API smoke validation passed"
  else
    fail "config API smoke validation failed; see $CLIENT_LOG and $BACKEND_LOG"
    {
      printf '\n--- client log ---\n'
      cat "$CLIENT_LOG" || true
      printf '\n--- backend log ---\n'
      cat "$BACKEND_LOG" || true
    } >> "$REPORT_FILE"
  fi
fi

if [[ -s "$CLIENT_LOG" ]]; then
  pass "client validation log captured: $CLIENT_LOG"
else
  warn "client validation log is empty: $CLIENT_LOG"
fi

if [[ -s "$BACKEND_LOG" ]]; then
  pass "backend validation log captured: $BACKEND_LOG"
else
  warn "backend validation log is empty: $BACKEND_LOG"
fi

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
printf 'Report: %s\n' "$REPORT_FILE"
printf 'Client log: %s\n' "$CLIENT_LOG"
printf 'Backend log: %s\n' "$BACKEND_LOG"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
