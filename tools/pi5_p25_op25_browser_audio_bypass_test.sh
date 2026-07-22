#!/usr/bin/env bash
# Run an OP25 browser-audio test using the raw bypass bridge.
# This intentionally ignores OP25 2-byte audio flags and does not run the
# encrypted-log gate. Use it only as an A/B troubleshooting baseline.
set -Eeuo pipefail

SECONDS_TO_RUN=300
HTTP_PORT=8072
UDP_PORT=23456
OP25_VERBOSITY=0
YES=0
REPORT_DIR=".p25_browser_audio_bypass_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/browser_audio_bypass_${STAMP}.txt"
BRIDGE_LOG="$REPORT_DIR/raw_bypass_bridge_${STAMP}.log"
OP25_LOG="$REPORT_DIR/op25_raw_bypass_${STAMP}.log"
AUDIO_STATUS_JSON="$REPORT_DIR/audio_status_${STAMP}.json"
MARKER="runtime/settings/op25_validated_rx_command.env"
BRIDGE_PID=""
OP25_PID=""

usage() {
  cat <<'EOF_USAGE'
Usage:
  ./tools/pi5_p25_op25_browser_audio_bypass_test.sh [options]

Runs a raw browser-audio A/B test on the Pi:
  - stops the backend scanner process to free the SDR,
  - starts a separate raw bypass browser audio bridge on port 8072,
  - starts OP25 directly from the validated marker with UDP audio enabled,
  - ignores OP25 2-byte audio flags for playback,
  - does not run the encrypted-log audio gate watcher.

Options:
  --seconds N          Test duration. Default: 300
  --http-port N        Browser audio HTTP port. Default: 8072
  --udp-port N         OP25 UDP PCM port. Default: 23456
  --op25-verbosity N   OP25 -v value. Default: 0
  --yes                Required to run the live test
  -h, --help           Show help

Open this during the test:
  http://<pi-ip>:8072/audio.wav
EOF_USAGE
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --seconds) shift; SECONDS_TO_RUN="$1"; shift ;;
    --http-port) shift; HTTP_PORT="$1"; shift ;;
    --udp-port) shift; UDP_PORT="$1"; shift ;;
    --op25-verbosity) shift; OP25_VERBOSITY="$1"; shift ;;
    --yes) YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "FAIL: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"
pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; }

terminate_pid() {
  local pid="$1"
  local label="$2"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi
  kill "$pid" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  warn "forcing ${label} pid=$pid with SIGKILL"
  kill -KILL "$pid" >/dev/null 2>&1 || true
  sleep 1
}

cleanup() {
  if [[ -n "$OP25_PID" ]] && kill -0 "$OP25_PID" >/dev/null 2>&1; then
    terminate_pid "$OP25_PID" "OP25 raw bypass test" || true
  fi
  if [[ -n "$BRIDGE_PID" ]] && kill -0 "$BRIDGE_PID" >/dev/null 2>&1; then
    terminate_pid "$BRIDGE_PID" "raw bypass browser audio bridge" || true
  fi
}
trap cleanup EXIT

printf '=== PI-P25-SCANNER V0.3M raw browser audio bypass test ===\n' | tee -a "$REPORT_FILE"
printf 'Started UTC: %s\n' "$STAMP" | tee -a "$REPORT_FILE"
printf 'Working directory: %s\n' "$(pwd)" | tee -a "$REPORT_FILE"

if [[ "$YES" -ne 1 ]]; then
  fail "live audio bypass test requires --yes"
  exit 2
fi
for numeric in SECONDS_TO_RUN HTTP_PORT UDP_PORT OP25_VERBOSITY; do
  value="${!numeric}"
  if ! [[ "$value" =~ ^[0-9]+$ ]]; then
    fail "${numeric} must be a non-negative integer"
    exit 2
  fi
done
if [[ "$SECONDS_TO_RUN" -le 0 || "$HTTP_PORT" -le 0 || "$UDP_PORT" -le 0 ]]; then
  fail "seconds and ports must be positive"
  exit 2
fi
if [[ ! -f "DEV_GUARDRAILS.md" || ! -d "tools" ]]; then
  fail "run from PI-P25-SCANNER repository root on the Pi"
  exit 1
fi
pass "running from repository root"
if [[ ! -f "$MARKER" ]]; then
  fail "validated OP25 marker missing: $MARKER"
  exit 1
fi
pass "validated OP25 marker present"
if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 missing"
  exit 1
fi
pass "python3 available"
if ! command -v ss >/dev/null 2>&1; then
  fail "ss missing"
  exit 1
fi
pass "ss available"
if ! python3 tools/pi5_p25_browser_audio_raw_bypass_bridge.py --self-test >>"$REPORT_FILE" 2>&1; then
  fail "raw bypass bridge self-test failed"
  exit 1
fi
pass "raw bypass bridge self-test passed"

# shellcheck disable=SC1090
set -a
. "$MARKER"
set +a
required=(
  P25_VALIDATED_RX_APP
  P25_VALIDATED_RX_APP_DIR
  P25_VALIDATED_RX_PYTHONPATH
  P25_VALIDATED_RX_ARGS
  P25_VALIDATED_RX_SAMPLE_RATE
  P25_VALIDATED_RX_GAIN
  P25_VALIDATED_RX_PPM
  P25_VALIDATED_RX_TRUNK_TSV
)
for key in "${required[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    fail "marker is missing $key"
    exit 1
  fi
done
pass "validated OP25 marker fields present"

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [[ -z "$LAN_IP" ]]; then
  LAN_IP="$(hostname)"
fi
AUDIO_URL="http://${LAN_IP}:${HTTP_PORT}/audio.wav"
STATUS_URL="http://${LAN_IP}:${HTTP_PORT}/api/audio/status"
TEST_URL="http://${LAN_IP}:${HTTP_PORT}/test-tone.wav"

printf 'BROWSER_AUDIO_URL=%s\n' "$AUDIO_URL" | tee -a "$REPORT_FILE"
printf 'BROWSER_AUDIO_STATUS=%s\n' "$STATUS_URL" | tee -a "$REPORT_FILE"
printf 'BROWSER_AUDIO_TEST_TONE=%s\n' "$TEST_URL" | tee -a "$REPORT_FILE"
printf 'AUDIO_BRIDGE_MODE=raw-bypass-v0.3m\n' | tee -a "$REPORT_FILE"
printf 'AUDIO_GATES_ENABLED=0\n' | tee -a "$REPORT_FILE"
printf 'OP25_VERBOSITY=%s\n' "$OP25_VERBOSITY" | tee -a "$REPORT_FILE"
printf '\nOpen BROWSER_AUDIO_URL in the browser while this script is running.\n\n' | tee -a "$REPORT_FILE"

python3 - <<'PY_STOP' >>"$REPORT_FILE" 2>&1 || true
import urllib.request
try:
    req = urllib.request.Request('http://127.0.0.1:8070/api/scanner/stop', method='POST')
    with urllib.request.urlopen(req, timeout=3) as resp:
        print('Backend scanner stop status:', resp.status)
except Exception as exc:
    print('Backend scanner stop skipped/error:', exc)
PY_STOP
pass "backend scanner stop requested"

bridge_pids() {
  ps -eo pid=,args= | awk '/pi5_p25_browser_audio_(raw_bypass_)?bridge_server[.]py|pi5_p25_browser_audio_raw_bypass_bridge[.]py/ {print $1}'
}
stop_stale_bridges() {
  local pid cmd found=0
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    found=1
    cmd="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
    warn "stopping stale browser audio bridge pid=${pid}: ${cmd:-unknown command}"
    terminate_pid "$pid" "stale browser audio bridge" || true
  done < <(bridge_pids)
  if [[ "$found" -eq 0 ]]; then
    pass "no stale browser audio bridge processes found"
  else
    pass "stale browser audio bridge processes stopped"
  fi
}
port_listeners() {
  local port="$1"
  ss -ltnp | awk -v p=":${port}" '$4 ~ p"$" {print}' || true
}
stop_stale_bridges
sleep 1
if [[ -n "$(port_listeners "$HTTP_PORT")" ]]; then
  warn "HTTP port $HTTP_PORT still busy after cleanup; forcing matching bridge processes once more"
  stop_stale_bridges || true
  sleep 1
fi
if [[ -n "$(port_listeners "$HTTP_PORT")" ]]; then
  fail "HTTP port $HTTP_PORT is still in use before raw bypass bridge start"
  port_listeners "$HTTP_PORT" | tee -a "$REPORT_FILE"
  exit 1
fi
pass "HTTP port $HTTP_PORT is free before bridge start"

python3 tools/pi5_p25_browser_audio_raw_bypass_bridge.py \
  --host 0.0.0.0 \
  --port "$HTTP_PORT" \
  --udp-host 127.0.0.1 \
  --udp-port "$UDP_PORT" \
  >"$BRIDGE_LOG" 2>&1 &
BRIDGE_PID=$!

BRIDGE_READY=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if ! kill -0 "$BRIDGE_PID" >/dev/null 2>&1; then
    break
  fi
  if python3 - "$HTTP_PORT" <<'PY_READY' >/dev/null 2>&1
import json
import sys
import urllib.request
port = sys.argv[1]
with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/audio/status', timeout=1) as resp:
    data = json.loads(resp.read().decode('utf-8'))
if not data.get('ok') or data.get('mode') != 'raw-bypass-v0.3m':
    raise SystemExit(1)
PY_READY
  then
    BRIDGE_READY=1
    break
  fi
  sleep 0.5
done
if [[ "$BRIDGE_READY" -ne 1 ]]; then
  fail "raw bypass browser audio bridge did not become ready; see $BRIDGE_LOG"
  if [[ -f "$BRIDGE_LOG" ]]; then
    printf '\n--- bridge log tail ---\n' | tee -a "$REPORT_FILE"
    tail -80 "$BRIDGE_LOG" | tee -a "$REPORT_FILE"
  fi
  exit 1
fi
pass "raw bypass browser audio bridge ready pid=$BRIDGE_PID"

OP25_CMD=(
  "$P25_VALIDATED_RX_APP"
  --args "$P25_VALIDATED_RX_ARGS"
  -S "$P25_VALIDATED_RX_SAMPLE_RATE"
  -q "$P25_VALIDATED_RX_PPM"
  -N "$P25_VALIDATED_RX_GAIN"
  -T "$P25_VALIDATED_RX_TRUNK_TSV"
  -V
  -2
)
if [[ "$OP25_VERBOSITY" -gt 0 ]]; then
  OP25_CMD+=(-v "$OP25_VERBOSITY")
fi
if [[ -n "${P25_VALIDATED_RX_TERMINAL:-}" ]]; then
  OP25_CMD+=(-l "$P25_VALIDATED_RX_TERMINAL")
fi
if [[ -n "${P25_VALIDATED_RX_CRYPT_BEHAVIOR:-}" ]]; then
  OP25_CMD+=(--crypt-behavior "$P25_VALIDATED_RX_CRYPT_BEHAVIOR")
fi
OP25_CMD+=(-w -W 127.0.0.1 -u "$UDP_PORT")

{
  printf 'OP25 raw bypass command:'
  printf ' %q' "${OP25_CMD[@]}"
  printf '\n'
} | tee -a "$REPORT_FILE"

(
  cd "$P25_VALIDATED_RX_APP_DIR"
  if command -v stdbuf >/dev/null 2>&1; then
    PYTHONPATH="$P25_VALIDATED_RX_PYTHONPATH" stdbuf -oL -eL timeout "$SECONDS_TO_RUN" "${OP25_CMD[@]}"
  else
    PYTHONPATH="$P25_VALIDATED_RX_PYTHONPATH" timeout "$SECONDS_TO_RUN" "${OP25_CMD[@]}"
  fi
) >"$OP25_LOG" 2>&1 &
OP25_PID=$!
pass "OP25 raw bypass command started pid=$OP25_PID for ${SECONDS_TO_RUN}s"

printf '\nListening window active for %s seconds.\n' "$SECONDS_TO_RUN" | tee -a "$REPORT_FILE"
printf 'Open now: %s\n\n' "$AUDIO_URL" | tee -a "$REPORT_FILE"

START_EPOCH="$(date +%s)"
while kill -0 "$OP25_PID" >/dev/null 2>&1; do
  NOW="$(date +%s)"
  ELAPSED=$((NOW - START_EPOCH))
  if (( ELAPSED >= SECONDS_TO_RUN )); then
    break
  fi
  if (( ELAPSED % 15 == 0 )); then
    python3 - "$HTTP_PORT" <<'PY_STATUS' 2>/dev/null | tee -a "$REPORT_FILE" || true
import json
import sys
import urllib.request
port = sys.argv[1]
try:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/audio/status', timeout=2) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    print('AUDIO_STATUS', json.dumps({
        'mode': data.get('mode'),
        'packets': data.get('packets'),
        'audio_packets': data.get('audio_packets'),
        'flag_packets': data.get('flag_packets'),
        'flag_zero_count': data.get('flag_zero_count'),
        'gates_enabled': data.get('gates_enabled'),
        'queued_chunks': data.get('queued_chunks'),
        'chunks_sent': data.get('chunks_sent'),
        'silence_chunks_sent': data.get('silence_chunks_sent'),
        'stream_clients': data.get('stream_clients'),
        'last_audio_age_seconds': data.get('last_audio_age_seconds'),
    }, sort_keys=True))
except Exception as exc:
    print('AUDIO_STATUS_ERROR', exc)
PY_STATUS
  fi
  sleep 1
done

wait "$OP25_PID" >/dev/null 2>&1 || true
OP25_PID=""

python3 - "$HTTP_PORT" "$AUDIO_STATUS_JSON" <<'PY_FINAL' | tee -a "$REPORT_FILE" || true
import json
import sys
import urllib.request
port = sys.argv[1]
out = sys.argv[2]
try:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/audio/status', timeout=2) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    with open(out, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write('\n')
    print('FINAL_AUDIO_STATUS', json.dumps(data, indent=2, sort_keys=True))
    if int(data.get('audio_packets') or 0) > 0:
        print('PASS: OP25 UDP audio packets were received by raw bypass bridge')
    else:
        print('WARN: no OP25 UDP audio packets were received during the window')
except Exception as exc:
    print('FAIL: final raw bypass bridge status failed:', exc)
PY_FINAL

python3 - "$OP25_LOG" "$AUDIO_STATUS_JSON" <<'PY_ANALYZE' | tee -a "$REPORT_FILE" || true
import json
import re
import sys
from pathlib import Path
op25_log = Path(sys.argv[1])
status_path = Path(sys.argv[2])
text = op25_log.read_text(encoding='utf-8', errors='replace') if op25_log.exists() else ''
lines = text.splitlines()
status = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
enc_patterns = ['CIPHERTXT', 'p25_crypt_algs', 'skip encrypted call', 'encrypted skip', 'algorithm module not found', 'algid=']
voice_patterns = ['IMBE', 'AMBE', 'voice update', 'grant']
clear_patterns = ['CLEARTEXT', 'PLAINTEXT']
enc_lines = [line for line in lines if any(p.lower() in line.lower() for p in enc_patterns)]
voice_lines = [line for line in lines if any(p.lower() in line.lower() for p in voice_patterns)]
clear_lines = [line for line in lines if any(p.lower() in line.lower() for p in clear_patterns)]
err_values = []
for line in lines:
    for match in re.finditer(r'\berrs\s+(\d+)', line):
        err_values.append(int(match.group(1)))
print('=== V0.3M Raw Bypass Classifier ===')
print('QUALITY_CLASSIFICATION=RAW_BYPASS_NO_PROJECT_AUDIO_GATES')
print(f"OP25_LOG_LINES={len(lines)}")
print(f"OP25_LINE_COUNTS encrypted={len(enc_lines)} voice={len(voice_lines)} clear_or_plain={len(clear_lines)}")
print(f"IMBE_ERR_COUNT={len(err_values)} IMBE_ERR_MAX={max(err_values) if err_values else None} IMBE_ERR_AVG={round(sum(err_values)/len(err_values),3) if err_values else None}")
print('BRIDGE_COUNTS packets={packets} audio_packets={audio_packets} flag_packets={flag_packets} chunks_sent={chunks_sent} stream_clients={stream_clients} gates_enabled={gates_enabled}'.format(**status))
if clear_lines:
    print('CLEAR_OR_PLAIN_SAMPLES:')
    for line in clear_lines[:8]:
        print('- ' + line[:220])
if enc_lines:
    print('ENCRYPTION_SAMPLES:')
    for line in enc_lines[:8]:
        print('- ' + line[:220])
print('FINAL_RAW_BYPASS_CLASSIFIER: PASS')
PY_ANALYZE

printf 'AUDIO_STATUS_JSON=%s\n' "$AUDIO_STATUS_JSON" | tee -a "$REPORT_FILE"
printf '\nBridge log: %s\n' "$BRIDGE_LOG" | tee -a "$REPORT_FILE"
printf 'OP25 log: %s\n' "$OP25_LOG" | tee -a "$REPORT_FILE"
printf 'Audio status JSON: %s\n' "$AUDIO_STATUS_JSON" | tee -a "$REPORT_FILE"
printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
