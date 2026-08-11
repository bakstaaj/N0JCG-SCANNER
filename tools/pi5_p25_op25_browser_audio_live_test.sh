#!/usr/bin/env bash
set -Eeuo pipefail

SECONDS_TO_RUN=120
HTTP_PORT=8072
UDP_PORT=23456
PREBUFFER_CHUNKS=0
DECLICK_SAMPLES=0
FLAG_DROP_HOLD_MS=2500
ENCRYPTED_LOG_HOLD_MS=5000
OP25_VERBOSITY=10
DISABLE_LOG_GATE=0
YES=0
REPORT_DIR=".p25_browser_audio_live_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/browser_audio_live_${STAMP}.txt"
BRIDGE_LOG="$REPORT_DIR/browser_audio_bridge_${STAMP}.log"
OP25_LOG="$REPORT_DIR/op25_audio_${STAMP}.log"
WATCHER_LOG="$REPORT_DIR/op25_audio_gate_watcher_${STAMP}.log"
WATCHER_JSON="$REPORT_DIR/op25_audio_gate_watcher_${STAMP}.json"
AUDIO_STATUS_JSON="$REPORT_DIR/audio_status_${STAMP}.json"
QUALITY_JSON="$REPORT_DIR/audio_quality_${STAMP}.json"
MARKER="runtime/settings/op25_validated_rx_command.env"
BRIDGE_PID=""
OP25_PID=""
WATCHER_PID=""

usage() {
  cat <<'EOF_USAGE'
Usage:
  ./tools/pi5_p25_op25_browser_audio_live_test.sh [options]

Runs a bounded browser-audio listening test on the Pi:
  - stops the backend scanner process to free the SDR,
  - force-cleans stale PI-P25 browser-audio bridge processes if required,
  - starts the encrypted-log-gated browser audio bridge on port 8072,
  - watches the OP25 log for encrypted-call indicators,
  - starts OP25 directly from the validated marker with UDP audio enabled,
  - keeps the stream available for the requested duration.

Options:
  --seconds N                 Test duration. Default: 120
  --http-port N               Browser audio HTTP port. Default: 8072
  --udp-port N                OP25 UDP PCM port. Default: 23456
  --flag-drop-hold-ms N       Hold after OP25 2-byte audio flags. Default: 2500
  --encrypted-log-hold-ms N   Hold after OP25 encrypted log indicators. Default: 5000
  --op25-verbosity N          OP25 -v value. Default: 10
  --disable-log-gate          Disable OP25 encrypted-log watcher
  --prebuffer-chunks N        Ignored compatibility option
  --declick-samples N         Ignored compatibility option
  --yes                       Required to run the live test
  -h, --help                  Show help

Open this during the test:
  http://<pi-ip>:8072/audio.wav
EOF_USAGE
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --seconds) shift; SECONDS_TO_RUN="$1"; shift ;;
    --http-port) shift; HTTP_PORT="$1"; shift ;;
    --udp-port) shift; UDP_PORT="$1"; shift ;;
    --flag-drop-hold-ms) shift; FLAG_DROP_HOLD_MS="$1"; shift ;;
    --encrypted-log-hold-ms) shift; ENCRYPTED_LOG_HOLD_MS="$1"; shift ;;
    --op25-verbosity) shift; OP25_VERBOSITY="$1"; shift ;;
    --disable-log-gate) DISABLE_LOG_GATE=1; shift ;;
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
  if [[ -n "$WATCHER_PID" ]] && kill -0 "$WATCHER_PID" >/dev/null 2>&1; then
    terminate_pid "$WATCHER_PID" "OP25 encrypted log gate watcher" || true
  fi
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

printf '=== scanner V0.3K encrypted-log-gated OP25 browser audio quality test ===\n' | tee -a "$REPORT_FILE"
printf 'Started UTC: %s\n' "$STAMP" | tee -a "$REPORT_FILE"
printf 'Working directory: %s\n' "$(pwd)" | tee -a "$REPORT_FILE"

if [[ "$YES" -ne 1 ]]; then
  fail "live audio test requires --yes"
  exit 2
fi
for numeric in SECONDS_TO_RUN HTTP_PORT UDP_PORT PREBUFFER_CHUNKS DECLICK_SAMPLES FLAG_DROP_HOLD_MS ENCRYPTED_LOG_HOLD_MS OP25_VERBOSITY; do
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
  fail "run from scanner repository root on the Pi"
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
  fail "encrypted-log-gate browser audio bridge self-test failed"
  exit 1
fi
pass "encrypted-log-gate browser audio bridge self-test passed"
if ! python3 -m py_compile tools/pi5_p25_op25_audio_gate_watcher.py >>"$REPORT_FILE" 2>&1; then
  fail "OP25 audio gate watcher compile failed"
  exit 1
fi
pass "OP25 audio gate watcher compile passed"

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
BRIDGE_URL="http://127.0.0.1:${HTTP_PORT}"

printf 'BROWSER_AUDIO_URL=%s\n' "$AUDIO_URL" | tee -a "$REPORT_FILE"
printf 'BROWSER_AUDIO_STATUS=%s\n' "$STATUS_URL" | tee -a "$REPORT_FILE"
printf 'BROWSER_AUDIO_TEST_TONE=%s\n' "$TEST_URL" | tee -a "$REPORT_FILE"
printf 'AUDIO_BRIDGE_MODE=encrypted-log-gate-v0.3k\n' | tee -a "$REPORT_FILE"
printf 'FLAG_DROP_HOLD_MS=%s\n' "$FLAG_DROP_HOLD_MS" | tee -a "$REPORT_FILE"
printf 'ENCRYPTED_LOG_HOLD_MS=%s\n' "$ENCRYPTED_LOG_HOLD_MS" | tee -a "$REPORT_FILE"
printf 'OP25_VERBOSITY=%s\n' "$OP25_VERBOSITY" | tee -a "$REPORT_FILE"
printf 'LOG_GATE_ENABLED=%s\n' "$((1 - DISABLE_LOG_GATE))" | tee -a "$REPORT_FILE"
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
  warn "HTTP port $HTTP_PORT still busy after cleanup; forcing matching bridge processes once more"
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
  --flag-drop-hold-ms "$FLAG_DROP_HOLD_MS" \
  --encrypted-log-hold-ms "$ENCRYPTED_LOG_HOLD_MS" \
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
  printf 'OP25 audio command:'
  printf ' %q' "${OP25_CMD[@]}"
  printf '\n'
} | tee -a "$REPORT_FILE"

: > "$OP25_LOG"
if [[ "$DISABLE_LOG_GATE" -eq 0 ]]; then
  python3 tools/pi5_p25_op25_audio_gate_watcher.py \
    --op25-log "$OP25_LOG" \
    --bridge-url "$BRIDGE_URL" \
    --hold-ms "$ENCRYPTED_LOG_HOLD_MS" \
    --duration "$SECONDS_TO_RUN" \
    --summary-file "$WATCHER_JSON" \
    >"$WATCHER_LOG" 2>&1 &
  WATCHER_PID=$!
  pass "OP25 encrypted log gate watcher started pid=$WATCHER_PID"
else
  warn "OP25 encrypted log gate watcher disabled"
fi

(
  cd "$P25_VALIDATED_RX_APP_DIR"
  if command -v stdbuf >/dev/null 2>&1; then
    PYTHONPATH="$P25_VALIDATED_RX_PYTHONPATH" stdbuf -oL -eL timeout "$SECONDS_TO_RUN" "${OP25_CMD[@]}"
  else
    PYTHONPATH="$P25_VALIDATED_RX_PYTHONPATH" timeout "$SECONDS_TO_RUN" "${OP25_CMD[@]}"
  fi
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
        'flag_packets': data.get('flag_packets'),
        'flag_zero_count': data.get('flag_zero_count'),
        'audio_dropped_by_flag': data.get('audio_dropped_by_flag'),
        'log_gate_events': data.get('log_gate_events'),
        'audio_dropped_by_log_gate': data.get('audio_dropped_by_log_gate'),
        'log_gate_active': data.get('log_gate_active'),
        'queued_chunks': data.get('queued_chunks'),
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

if [[ -n "$WATCHER_PID" ]] && kill -0 "$WATCHER_PID" >/dev/null 2>&1; then
  terminate_pid "$WATCHER_PID" "OP25 encrypted log gate watcher" || true
fi
WATCHER_PID=""

python3 - "$HTTP_PORT" "$AUDIO_STATUS_JSON" <<'PY' | tee -a "$REPORT_FILE" || true
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
    if int(data.get('audio_packets') or 0) > 0 or int(data.get('flag_packets') or 0) > 0:
        print('PASS: OP25 UDP audio/flag activity was received by encrypted-log-gated browser audio bridge')
    else:
        print('WARN: no OP25 UDP audio/flag activity was received during the window')
except Exception as exc:
    print('FAIL: final audio bridge status failed:', exc)
PY
printf 'AUDIO_STATUS_JSON=%s\n' "$AUDIO_STATUS_JSON" | tee -a "$REPORT_FILE"

python3 - "$OP25_LOG" "$AUDIO_STATUS_JSON" "$WATCHER_JSON" "$QUALITY_JSON" <<'PY' | tee -a "$REPORT_FILE" || true
import json
import re
import sys
from pathlib import Path
op25_log = Path(sys.argv[1])
audio_status_json = Path(sys.argv[2])
watcher_json = Path(sys.argv[3])
quality_json = Path(sys.argv[4])
text = op25_log.read_text(encoding='utf-8', errors='replace') if op25_log.exists() else ''
lines = text.splitlines()
enc_patterns = ['CIPHERTXT', 'p25_crypt_algs', 'skip encrypted call', 'encrypted skip', 'algorithm module not found', 'algid=']
voice_patterns = ['IMBE', 'AMBE', 'voice update', 'grant']
error_patterns = ['timeout', 'expired', 'error', 'sync', 'crc']
enc_lines = [line for line in lines if any(p.lower() in line.lower() for p in enc_patterns)]
voice_lines = [line for line in lines if any(p.lower() in line.lower() for p in voice_patterns)]
err_values = []
rs_values = []
for line in lines:
    for match in re.finditer(r'\berrs\s+(\d+)', line):
        err_values.append(int(match.group(1)))
    for match in re.finditer(r'rs_errs=(\d+)', line):
        rs_values.append(int(match.group(1)))
status = {}
if audio_status_json.exists():
    status = json.loads(audio_status_json.read_text(encoding='utf-8'))
watcher = {}
if watcher_json.exists():
    watcher = json.loads(watcher_json.read_text(encoding='utf-8'))
classification = 'AUDIO_QUALITY_INCONCLUSIVE'
if int(status.get('audio_dropped_by_log_gate') or 0) > 0 and len(enc_lines) > 0:
    classification = 'ENCRYPTED_AUDIO_SUPPRESSED_BY_OP25_LOG_GATE'
elif int(status.get('audio_dropped_by_flag') or 0) > 0 and len(enc_lines) > 0:
    classification = 'ENCRYPTED_OR_INVALID_AUDIO_SUPPRESSED_BY_FLAGS'
elif len(enc_lines) > 0 and int(status.get('audio_packets') or 0) > 0:
    classification = 'POSSIBLE_ENCRYPTED_BURSTS_REMAIN'
elif err_values and (max(err_values) >= 8 or (sum(err_values) / len(err_values)) >= 3):
    classification = 'LIKELY_RF_OR_SIMULCAST_DECODE_ERRORS'
summary = {
    'classification': classification,
    'op25_log_lines': len(lines),
    'encrypted_line_count': len(enc_lines),
    'voice_line_count': len(voice_lines),
    'generic_error_count': sum(1 for line in lines if any(p in line.lower() for p in error_patterns)),
    'imbe_err_count': len(err_values),
    'imbe_err_max': max(err_values) if err_values else None,
    'imbe_err_avg': round(sum(err_values) / len(err_values), 3) if err_values else None,
    'rs_err_count': len(rs_values),
    'rs_err_max': max(rs_values) if rs_values else None,
    'rs_err_avg': round(sum(rs_values) / len(rs_values), 3) if rs_values else None,
    'audio_packets': status.get('audio_packets'),
    'flag_packets': status.get('flag_packets'),
    'audio_dropped_by_flag': status.get('audio_dropped_by_flag'),
    'log_gate_events': status.get('log_gate_events'),
    'audio_dropped_by_log_gate': status.get('audio_dropped_by_log_gate'),
    'watcher_encrypted_matches': watcher.get('encrypted_matches'),
    'watcher_gate_requests': watcher.get('gate_requests'),
    'watcher_gate_errors': watcher.get('gate_errors'),
    'encryption_samples': enc_lines[:8],
    'error_samples': [line for line in lines if any(p in line.lower() for p in error_patterns)][:8],
}
quality_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
print('=== V0.3K Audio Quality Classifier ===')
print(f"QUALITY_CLASSIFICATION={classification}")
print(f"OP25_LOG_LINES={summary['op25_log_lines']}")
print(f"OP25_LINE_COUNTS encrypted={summary['encrypted_line_count']} voice={summary['voice_line_count']} generic_error={summary['generic_error_count']}")
print(f"IMBE_ERR_COUNT={summary['imbe_err_count']} IMBE_ERR_MAX={summary['imbe_err_max']} IMBE_ERR_AVG={summary['imbe_err_avg']}")
print(f"RS_ERR_COUNT={summary['rs_err_count']} RS_ERR_MAX={summary['rs_err_max']} RS_ERR_AVG={summary['rs_err_avg']}")
print('BRIDGE_COUNTS packets={packets} audio_packets={audio_packets} flag_packets={flag_packets} audio_dropped_by_flag={audio_dropped_by_flag} log_gate_events={log_gate_events} audio_dropped_by_log_gate={audio_dropped_by_log_gate} silence_chunks_sent={silence_chunks_sent}'.format(**status))
print('WATCHER_COUNTS encrypted_matches={encrypted_matches} gate_requests={gate_requests} gate_errors={gate_errors}'.format(**{'encrypted_matches': watcher.get('encrypted_matches'), 'gate_requests': watcher.get('gate_requests'), 'gate_errors': watcher.get('gate_errors')}))
if summary['encryption_samples']:
    print('ENCRYPTION_SAMPLES:')
    for line in summary['encryption_samples']:
        print('- ' + line[:220])
print(f'QUALITY_JSON={quality_json}')
print('FINAL_QUALITY_CLASSIFIER: PASS')
PY
printf 'QUALITY_JSON=%s\n' "$QUALITY_JSON" | tee -a "$REPORT_FILE"
if [[ -f "$WATCHER_JSON" ]]; then
  printf 'WATCHER_JSON=%s\n' "$WATCHER_JSON" | tee -a "$REPORT_FILE"
fi

printf '\nBridge log: %s\n' "$BRIDGE_LOG" | tee -a "$REPORT_FILE"
printf 'OP25 log: %s\n' "$OP25_LOG" | tee -a "$REPORT_FILE"
printf 'Watcher log: %s\n' "$WATCHER_LOG" | tee -a "$REPORT_FILE"
printf 'Audio status JSON: %s\n' "$AUDIO_STATUS_JSON" | tee -a "$REPORT_FILE"
printf 'Quality JSON: %s\n' "$QUALITY_JSON" | tee -a "$REPORT_FILE"
printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
