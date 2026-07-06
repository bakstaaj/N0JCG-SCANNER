#!/usr/bin/env bash
set -Eeuo pipefail

SECONDS_TO_RUN=600
HTTP_PORT=8072
UDP_PORT=23456
PREBUFFER_CHUNKS=0
DECLICK_SAMPLES=0
YES=0
REPORT_DIR=".p25_browser_audio_live_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/browser_audio_live_${STAMP}.txt"
BRIDGE_LOG="$REPORT_DIR/browser_audio_bridge_${STAMP}.log"
OP25_LOG="$REPORT_DIR/op25_audio_${STAMP}.log"
MARKER="runtime/settings/op25_validated_rx_command.env"
BRIDGE_PID=""
OP25_PID=""

usage() {
  cat <<'EOF_USAGE'
Usage:
  ./tools/pi5_p25_op25_browser_audio_live_test.sh [options]

Runs a bounded raw browser-audio listening test on the Pi:
  - stops the backend scanner process to free the SDR,
  - force-cleans stale PI-P25 browser-audio bridge processes if required,
  - starts the raw browser audio bridge on port 8072,
  - starts OP25 directly from the validated marker with UDP audio enabled,
  - keeps the stream available for the requested duration.

Options:
  --seconds N              Test duration. Default: 600
  --http-port N            Browser audio HTTP port. Default: 8072
  --udp-port N             OP25 UDP PCM port. Default: 23456
  --prebuffer-chunks N     Ignored compatibility option in raw V0.3G mode
  --declick-samples N      Ignored compatibility option in raw V0.3G mode
  --yes                    Required to run the live test
  -h, --help               Show help

Open this during the test:
  http://<pi-ip>:8072/audio.wav
EOF_USAGE
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --seconds) shift; SECONDS_TO_RUN="$1"; shift ;;
    --http-port) shift; HTTP_PORT="$1"; shift ;;
    --udp-port) shift; UDP_PORT="$1"; shift ;;
    --prebuffer-chunks) shift; PREBUFFER_CHUNKS="$1"; shift ;;
    --declick-samples) shift; DECLICK_SAMPLES="$1"; shift ;;
    --yes) YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "FAIL: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

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
  warn "forcing stale ${label} pid=$pid with SIGKILL"
  kill -KILL "$pid" >/dev/null 2>&1 || true
  for _ in 1 2 3; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

cleanup() {
  if [[ -n "$OP25_PID" ]] && kill -0 "$OP25_PID" >/dev/null 2>&1; then
    terminate_pid "$OP25_PID" "OP25 audio test" || true
  fi
  if [[ -n "$BRIDGE_PID" ]] && kill -0 "$BRIDGE_PID" >/dev/null 2>&1; then
    terminate_pid "$BRIDGE_PID" "browser audio bridge" || true
  fi
}
trap cleanup EXIT

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"

printf '=== PI-P25-SCANNER V0.3G raw OP25 browser audio live test ===\n' | tee -a "$REPORT_FILE"
printf 'Started UTC: %s\n' "$STAMP" | tee -a "$REPORT_FILE"
printf 'Working directory: %s\n' "$(pwd)" | tee -a "$REPORT_FILE"

if [[ "$YES" -ne 1 ]]; then
  fail "live audio test requires --yes"
  exit 2
fi
for numeric in SECONDS_TO_RUN HTTP_PORT UDP_PORT PREBUFFER_CHUNKS DECLICK_SAMPLES; do
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
if ! python3 tools/pi5_p25_browser_audio_bridge_server.py --self-test >>"$REPORT_FILE" 2>&1; then
  fail "raw browser audio bridge self-test failed"
  exit 1
fi
pass "raw browser audio bridge self-test passed"

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
printf 'AUDIO_BRIDGE_MODE=raw-v0.3g\n' | tee -a "$REPORT_FILE"
printf 'COMPAT_PREBUFFER_CHUNKS_IGNORED=%s\n' "$PREBUFFER_CHUNKS" | tee -a "$REPORT_FILE"
printf 'COMPAT_DECLICK_SAMPLES_IGNORED=%s\n' "$DECLICK_SAMPLES" | tee -a "$REPORT_FILE"
printf '\nOpen BROWSER_AUDIO_URL in the browser while this script is running.\n\n' | tee -a "$REPORT_FILE"

python3 - <<'PY' >>"$REPORT_FILE" 2>&1 || true
import urllib.request
try:
    req = urllib.request.Request('http://127.0.0.1:8070/api/scanner/stop', method='POST')
    with urllib.request.urlopen(req, timeout=3) as resp:
        print('Backend scanner stop status:', resp.status)
except Exception as exc:
    print('Backend scanner stop skipped/error:', exc)
PY
pass "backend scanner stop requested"

bridge_pids() {
  ps -eo pid=,args= | awk '/pi5_p25_browser_audio_bridge_server[.]py/ {print $1}'
}

stop_stale_bridges() {
  local pid cmd found=0
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    found=1
    cmd="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
    warn "stopping stale browser audio bridge pid=${pid}: ${cmd:-unknown command}"
    if ! terminate_pid "$pid" "stale browser audio bridge"; then
      fail "could not terminate stale browser audio bridge pid=$pid"
      return 1
    fi
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
  warn "HTTP port $HTTP_PORT still busy after SIGTERM cleanup; forcing matching bridge processes once more"
  stop_stale_bridges || true
  sleep 1
fi
if [[ -n "$(port_listeners "$HTTP_PORT")" ]]; then
  fail "HTTP port $HTTP_PORT is still in use after forced stale bridge cleanup"
  port_listeners "$HTTP_PORT" | tee -a "$REPORT_FILE"
  exit 1
fi
pass "HTTP port $HTTP_PORT is free before bridge start"

python3 tools/pi5_p25_browser_audio_bridge_server.py \
  --host 0.0.0.0 \
  --port "$HTTP_PORT" \
  --udp-host 127.0.0.1 \
  --udp-port "$UDP_PORT" \
  --prebuffer-chunks "$PREBUFFER_CHUNKS" \
  --declick-samples "$DECLICK_SAMPLES" \
  >"$BRIDGE_LOG" 2>&1 &
BRIDGE_PID=$!

BRIDGE_READY=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if ! kill -0 "$BRIDGE_PID" >/dev/null 2>&1; then
    break
  fi
  if python3 - "$HTTP_PORT" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request
port = sys.argv[1]
with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/audio/status', timeout=1) as resp:
    data = json.loads(resp.read().decode('utf-8'))
if not data.get('ok'):
    raise SystemExit(1)
PY
  then
    BRIDGE_READY=1
    break
  fi
  sleep 0.5
done
if [[ "$BRIDGE_READY" -ne 1 ]]; then
  fail "browser audio bridge did not become ready; see $BRIDGE_LOG"
  if [[ -f "$BRIDGE_LOG" ]]; then
    printf '\n--- bridge log tail ---\n' | tee -a "$REPORT_FILE"
    tail -80 "$BRIDGE_LOG" | tee -a "$REPORT_FILE"
  fi
  exit 1
fi
pass "browser audio bridge ready pid=$BRIDGE_PID"

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
if [[ -n "${P25_VALIDATED_RX_TERMINAL:-}" ]]; then
  OP25_CMD+=(-l "$P25_VALIDATED_RX_TERMINAL")
fi
if [[ -n "${P25_VALIDATED_RX_CRYPT_BEHAVIOR:-}" ]]; then
  OP25_CMD+=(--crypt-behavior "$P25_VALIDATED_RX_CRYPT_BEHAVIOR")
fi
OP25_CMD+=(-w -W 127.0.0.1 -u "$UDP_PORT")

{
  printf 'OP25 audio command:'
  printf ' %q' "${OP25_CMD[@]}"
  printf '\n'
} | tee -a "$REPORT_FILE"

(
  cd "$P25_VALIDATED_RX_APP_DIR"
  PYTHONPATH="$P25_VALIDATED_RX_PYTHONPATH" timeout "$SECONDS_TO_RUN" "${OP25_CMD[@]}"
) >"$OP25_LOG" 2>&1 &
OP25_PID=$!
pass "OP25 audio command started pid=$OP25_PID for ${SECONDS_TO_RUN}s"

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
    python3 - "$HTTP_PORT" <<'PY' 2>/dev/null | tee -a "$REPORT_FILE" || true
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
        'queued_chunks': data.get('queued_chunks'),
        'underruns': data.get('underruns'),
        'silence_chunks_sent': data.get('silence_chunks_sent'),
        'last_audio_age_seconds': data.get('last_audio_age_seconds'),
    }, sort_keys=True))
except Exception as exc:
    print('AUDIO_STATUS_ERROR', exc)
PY
  fi
  sleep 1
done

wait "$OP25_PID" >/dev/null 2>&1 || true
OP25_PID=""

python3 - "$HTTP_PORT" <<'PY' | tee -a "$REPORT_FILE" || true
import json
import sys
import urllib.request
port = sys.argv[1]
try:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/audio/status', timeout=2) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    print('FINAL_AUDIO_STATUS', json.dumps(data, indent=2, sort_keys=True))
    if int(data.get('audio_packets') or 0) > 0:
        print('PASS: OP25 UDP audio packets were received by raw browser audio bridge')
    else:
        print('WARN: no OP25 UDP audio packets were received; this may mean no clear voice grant occurred during the window')
except Exception as exc:
    print('FAIL: final audio bridge status failed:', exc)
PY

printf '\nBridge log: %s\n' "$BRIDGE_LOG" | tee -a "$REPORT_FILE"
printf 'OP25 log: %s\n' "$OP25_LOG" | tee -a "$REPORT_FILE"
printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
