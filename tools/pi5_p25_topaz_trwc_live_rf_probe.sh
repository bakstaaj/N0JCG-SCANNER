#!/usr/bin/env bash
# Bounded live RF validation for the TOPAZ/TRWC test profile.
# Uses the installed backend/API on port 8070 and the validated OP25 command marker.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_topaz_trwc_live_rf_probe_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/topaz_trwc_live_rf_${STAMP}.txt"
CLIENT_LOG="$REPORT_DIR/client_${STAMP}.log"
STATUS_JSONL="$REPORT_DIR/status_samples_${STAMP}.jsonl"
SECONDS_LIMIT=90
PORT=8070
YES=0
LEAVE_RUNNING=0
STOP_ATTEMPTED=0

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

usage() {
  cat <<USAGE
Usage:
  ./tools/pi5_p25_topaz_trwc_live_rf_probe.sh --seconds 90 --yes

Options:
  --seconds N       Observation window in seconds. Default: 90. Allowed: 20-600.
  --port N          Backend API port. Default: 8070.
  --leave-running   Do not stop the decoder at the end of the probe.
  --yes             Required because the probe starts live OP25 decode via the backend API.
  -h, --help        Show this help.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --seconds)
      SECONDS_LIMIT="${2:-}"
      shift 2
      ;;
    --port)
      PORT="${2:-}"
      shift 2
      ;;
    --leave-running)
      LEAVE_RUNNING=1
      shift
      ;;
    --yes)
      YES=1
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
: > "$CLIENT_LOG"
: > "$STATUS_JSONL"
printf '=== scanner TOPAZ/TRWC live RF probe ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "runtime" ]]; then
  pass "running from repository root"
else
  fail "run from scanner repository root on the Raspberry Pi"
fi

if [[ "$YES" -eq 1 ]]; then
  pass "explicit --yes provided for live RF decode start"
else
  fail "--yes is required because this probe starts live OP25 decode"
fi

if ! [[ "$SECONDS_LIMIT" =~ ^[0-9]+$ ]] || [[ "$SECONDS_LIMIT" -lt 20 || "$SECONDS_LIMIT" -gt 600 ]]; then
  fail "--seconds must be an integer from 20 to 600"
else
  pass "observation window accepted: ${SECONDS_LIMIT}s"
fi

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [[ "$PORT" -lt 1 || "$PORT" -gt 65535 ]]; then
  fail "--port must be an integer from 1 to 65535"
else
  pass "backend API port selected: $PORT"
fi

for cmd in python3 timeout; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "command available: $cmd"
  else
    fail "missing required command: $cmd"
  fi
done

if [[ -f "runtime/settings/op25_validated_rx_command.env" ]]; then
  pass "validated OP25 command marker exists"
else
  fail "validated OP25 command marker missing; run tools/pi5_p25_op25_live_command_probe.sh --rx-smoke --seconds 20 --yes first"
fi

if PYTHONPATH=src python3 - "$REPORT_FILE" <<'PY_CONFIG'
from __future__ import annotations
import sys
from pathlib import Path
from pi_p25_scanner.config_store import load_active_project_config
report = Path(sys.argv[1])
cfg, path = load_active_project_config()
system = cfg.first_enabled_system()
control = system.receiver_roles.get("p25_control")
voice = system.receiver_roles.get("p25_voice")
freqs = set(system.control_channels_hz)
expected_any = {852750000, 852825000, 853275000, 853350000}
problems: list[str] = []
if "TOPAZ" not in system.name.upper() and "TRWC" not in system.name.upper():
    problems.append(f"active config name does not look like TOPAZ/TRWC: {system.name}")
if not (freqs & expected_any):
    problems.append(f"active config does not include expected TOPAZ/TRWC Mesa control channels: {sorted(freqs)}")
if not control or not control.rtl_serial:
    problems.append("p25_control RTL serial is not set")
if not voice or not voice.rtl_serial:
    problems.append("p25_voice RTL serial is not set")
with report.open("a", encoding="utf-8") as handle:
    handle.write(f"CONFIG_PATH={path}\n")
    handle.write(f"SYSTEM_NAME={system.name}\n")
    handle.write(f"CONTROL_CHANNELS_HZ={','.join(str(v) for v in system.control_channels_hz)}\n")
    handle.write(f"P25_CONTROL_SERIAL={control.rtl_serial if control else ''}\n")
    handle.write(f"P25_VOICE_SERIAL={voice.rtl_serial if voice else ''}\n")
if problems:
    print("; ".join(problems))
    raise SystemExit(1)
print("TOPAZ_CONFIG_OK")
PY_CONFIG
then
  pass "active runtime config looks like TOPAZ/TRWC with receiver roles"
else
  fail "active runtime config is not ready for TOPAZ/TRWC live test"
fi

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if PYTHONPATH=src python3 - "$PORT" "$SECONDS_LIMIT" "$LEAVE_RUNNING" "$CLIENT_LOG" "$STATUS_JSONL" <<'PY_LIVE'
from __future__ import annotations
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

port = int(sys.argv[1])
seconds = int(sys.argv[2])
leave_running = int(sys.argv[3]) == 1
client_log = Path(sys.argv[4])
status_jsonl = Path(sys.argv[5])
base = f"http://127.0.0.1:{port}"


def write(message: str) -> None:
    with client_log.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def request(path: str, method: str = "GET") -> dict:
    req = urllib.request.Request(base + path, method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
        body = response.read().decode("utf-8")
        payload = json.loads(body) if body else {}
    write(f"{method} {path} -> {response.status} {json.dumps(payload, sort_keys=True)[:3000]}")
    return payload

last_error: Exception | None = None
for _ in range(40):
    try:
        status = request("/api/status")
        if status.get("scanner_state"):
            break
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        last_error = exc
        time.sleep(0.25)
else:
    raise SystemExit(f"backend API on port {port} never became ready: {last_error}")

start = request("/api/scanner/start", "POST")
if start.get("scanner_state") not in {"running", "decoder_config_generated"}:
    raise SystemExit(f"unexpected start state: {start.get('scanner_state')}")
process = start.get("decoder_process", {})
if process.get("command_source") != "validated_marker":
    raise SystemExit(f"backend did not use validated marker: {process.get('command_source')}")
if not process.get("running"):
    raise SystemExit(f"decoder process was not reported running after start: {process}")

samples: list[dict] = []
end_time = time.time() + seconds
while time.time() < end_time:
    status = request("/api/status")
    status_jsonl.open("a", encoding="utf-8").write(json.dumps(status, sort_keys=True) + "\n")
    samples.append(status)
    if status.get("scanner_state") != "running" or not status.get("decoder_process", {}).get("running"):
        raise SystemExit(f"decoder stopped during observation: {status.get('scanner_state')}")
    time.sleep(2)

summary = {
    "samples": len(samples),
    "saw_control_frequency": any(s.get("active_control_frequency_hz") for s in samples),
    "saw_voice_frequency": any(s.get("active_voice_frequency_hz") for s in samples),
    "saw_tgid": any(s.get("active_tgid") for s in samples),
    "saw_phase": sorted({str(s.get("p25_phase")) for s in samples if s.get("p25_phase") and s.get("p25_phase") != "unknown"}),
    "saw_encrypted": any(bool(s.get("encrypted")) for s in samples),
    "saw_muted": any(bool(s.get("muted")) for s in samples),
    "last_state": samples[-1].get("scanner_state") if samples else "none",
    "last_event": samples[-1].get("last_event") if samples else "none",
}
write("SUMMARY " + json.dumps(summary, sort_keys=True))
print("TOPAZ_LIVE_RF_SUMMARY " + json.dumps(summary, sort_keys=True))

if not leave_running:
    stop = request("/api/scanner/stop", "POST")
    if stop.get("scanner_state") != "stopped":
        raise SystemExit(f"stop did not report stopped: {stop.get('scanner_state')}")
    print("TOPAZ_LIVE_RF_STOPPED")
else:
    print("TOPAZ_LIVE_RF_LEFT_RUNNING")

print("TOPAZ_LIVE_RF_PROBE_PASS")
PY_LIVE
then
  pass "backend/API TOPAZ live RF observation completed"
else
  fail "TOPAZ live RF API observation failed; see $CLIENT_LOG and $STATUS_JSONL"
fi

if [[ -s "$STATUS_JSONL" ]]; then
  pass "status sample log captured: $STATUS_JSONL"
else
  warn "status sample log is empty: $STATUS_JSONL"
fi

if grep -q 'TOPAZ_LIVE_RF_SUMMARY' "$CLIENT_LOG" 2>/dev/null; then
  pass "live RF summary captured in client log"
else
  warn "live RF summary missing from client log"
fi

if grep -Eq '"saw_tgid": true|"saw_voice_frequency": true|"saw_control_frequency": true' "$CLIENT_LOG" 2>/dev/null; then
  pass "runtime parser observed at least one decoded activity field during window"
else
  warn "no decoded TGID/frequency activity observed during bounded window; this can happen when the system is quiet or RF/control-channel decode is weak"
fi

printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'Client log: %s\n' "$CLIENT_LOG" | tee -a "$REPORT_FILE"
printf 'Status samples: %s\n' "$STATUS_JSONL" | tee -a "$REPORT_FILE"
printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
