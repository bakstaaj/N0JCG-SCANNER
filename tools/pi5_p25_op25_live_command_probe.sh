#!/usr/bin/env bash
# Bounded OP25 live command probe for PI-P25-SCANNER.
# Run from the Raspberry Pi repository root. Default mode is dry-run only.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_op25_live_command_probe_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/op25_live_command_probe_${STAMP}.txt"
COMMAND_FILE="$REPORT_DIR/op25_rx_command_${STAMP}.txt"
SMOKE_LOG="$REPORT_DIR/op25_rx_smoke_${STAMP}.log"
HELP_LOG="$REPORT_DIR/op25_rx_help_${STAMP}.txt"
META_ENV="$REPORT_DIR/op25_command_meta_${STAMP}.env"
MODE="dry-run"
SECONDS_LIMIT=20
YES=0
TERMINAL_TYPE="http:127.0.0.1:18091"
SAMPLE_RATE=960000
APP="rx"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

usage() {
  cat <<USAGE
Usage:
  ./tools/pi5_p25_op25_live_command_probe.sh --dry-run
  ./tools/pi5_p25_op25_live_command_probe.sh --rx-smoke --seconds 20 --yes

Options:
  --dry-run          Generate and print the candidate rx.py command only. Default.
  --rx-smoke         Run a bounded rx.py foreground smoke test with timeout.
  --seconds N        Smoke-test duration. Default: 20.
  --yes              Required with --rx-smoke.
  --terminal VALUE   OP25 terminal option. Default: http:127.0.0.1:18091.
  --sample-rate N    OP25 sample rate. Default: 960000.
  --app rx           Current executable probe target. multi_rx is reserved for a later validator.
  -h, --help         Show this help.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      MODE="dry-run"
      shift
      ;;
    --rx-smoke)
      MODE="rx-smoke"
      shift
      ;;
    --seconds)
      SECONDS_LIMIT="${2:-}"
      shift 2
      ;;
    --yes)
      YES=1
      shift
      ;;
    --terminal)
      TERMINAL_TYPE="${2:-}"
      shift 2
      ;;
    --sample-rate)
      SAMPLE_RATE="${2:-}"
      shift 2
      ;;
    --app)
      APP="${2:-}"
      shift 2
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

mkdir -p "$REPORT_DIR" runtime/settings runtime/op25
: > "$REPORT_FILE"
printf '=== PI-P25-SCANNER OP25 live command probe ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "tools" && -d "src/pi_p25_scanner" ]]; then
  pass "running from repository root"
else
  fail "run from PI-P25-SCANNER repository root"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]]; then
  pass "Linux host detected"
else
  fail "target runtime must be Linux on Raspberry Pi"
fi

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
fi

if command -v timeout >/dev/null 2>&1; then
  pass "timeout available"
else
  fail "timeout missing"
fi

if [[ "$APP" != "rx" ]]; then
  fail "only --app rx is supported in this milestone; multi_rx validation is reserved for a later patch"
fi

if ! [[ "$SECONDS_LIMIT" =~ ^[0-9]+$ ]] || [[ "$SECONDS_LIMIT" -lt 5 || "$SECONDS_LIMIT" -gt 120 ]]; then
  fail "--seconds must be an integer from 5 to 120"
fi

if [[ "$MODE" == "rx-smoke" && "$YES" -ne 1 ]]; then
  fail "--rx-smoke requires --yes"
fi

if [[ -f "runtime/settings/op25_source_path.env" ]]; then
  # shellcheck disable=SC1091
  source "runtime/settings/op25_source_path.env"
  pass "loaded OP25 source marker: runtime/settings/op25_source_path.env"
else
  warn "OP25 source marker missing; defaulting source dir to HOME/op25"
fi

SOURCE_DIR="${OP25_SOURCE_DIR:-${SOURCE_DIR:-$HOME/op25}}"
RX_PY="$SOURCE_DIR/op25/gr-op25_repeater/apps/rx.py"
MULTI_RX_PY="$SOURCE_DIR/op25/gr-op25_repeater/apps/multi_rx.py"

if [[ -d "$SOURCE_DIR" ]]; then
  pass "OP25 source directory exists: $SOURCE_DIR"
else
  fail "OP25 source directory missing: $SOURCE_DIR"
fi

if [[ -f "$RX_PY" ]]; then
  pass "OP25 rx.py exists: $RX_PY"
else
  fail "OP25 rx.py missing: $RX_PY"
fi

if [[ -f "$MULTI_RX_PY" ]]; then
  pass "OP25 multi_rx.py exists: $MULTI_RX_PY"
else
  warn "OP25 multi_rx.py missing: $MULTI_RX_PY"
fi

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if ./tools/p25_generate_op25_config.sh >> "$REPORT_FILE" 2>&1; then
  pass "generated PI-P25 OP25 runtime config"
else
  fail "failed to generate PI-P25 OP25 runtime config"
fi

if [[ -f "runtime/op25/trunk.tsv" ]]; then
  pass "generated trunk TSV exists: runtime/op25/trunk.tsv"
else
  fail "generated trunk TSV missing: runtime/op25/trunk.tsv"
fi

if PYTHONPATH=src python3 - "$META_ENV" <<'PY'
from __future__ import annotations
import shlex
import sys
from pathlib import Path
from pi_p25_scanner.config_store import load_active_project_config
from pi_p25_scanner.config_model import hz_to_mhz_string
out = Path(sys.argv[1])
cfg, path = load_active_project_config()
system = cfg.first_enabled_system()
control = system.receiver_roles.get("p25_control")
serial = control.rtl_serial if control else ""
gain = control.gain_db if control and control.gain_db is not None else 40.2
ppm = control.ppm if control else 0
gain_int = int(round(float(gain)))
control_hz = system.control_channels_hz[0]
lines = {
    "ACTIVE_CONFIG_PATH": str(path),
    "SYSTEM_NAME": system.name,
    "CONTROL_FREQUENCY_HZ": str(control_hz),
    "CONTROL_FREQUENCY_MHZ": hz_to_mhz_string(control_hz),
    "P25_CONTROL_SERIAL": serial,
    "P25_CONTROL_GAIN_DB": str(gain),
    "P25_CONTROL_GAIN_INT": str(gain_int),
    "P25_CONTROL_PPM": str(ppm),
    "TRUNK_TSV": "runtime/op25/trunk.tsv",
}
out.write_text("".join(f"{key}={shlex.quote(value)}\n" for key, value in lines.items()), encoding="utf-8")
PY
then
  pass "wrote command metadata: $META_ENV"
else
  fail "failed to load active config metadata"
fi

# shellcheck disable=SC1090
source "$META_ENV"

if [[ -n "${P25_CONTROL_SERIAL:-}" ]]; then
  pass "p25_control serial set: $P25_CONTROL_SERIAL"
else
  fail "p25_control serial is blank; run tools/p25_set_receiver_roles.sh first"
fi

if timeout 10s "$RX_PY" --help > "$HELP_LOG" 2>&1; then
  pass "rx.py help completed"
else
  rc=$?
  fail "rx.py --help failed rc=$rc; see $HELP_LOG"
fi

for opt in '--args' '-S' '-q' '-N' '-T' '-V' '-U' '--crypt-behavior' '-2' '-l'; do
  if grep -q -- "$opt" "$HELP_LOG"; then
    pass "rx.py help includes option: $opt"
  else
    fail "rx.py help missing expected option: $opt"
  fi
done

RX_CMD=(
  "$RX_PY"
  "--args" "rtl=$P25_CONTROL_SERIAL"
  "-S" "$SAMPLE_RATE"
  "-q" "$P25_CONTROL_PPM"
  "-N" "LNA:$P25_CONTROL_GAIN_INT"
  "-T" "$TRUNK_TSV"
  "-V"
  "-2"
  "-U"
  "-l" "$TERMINAL_TYPE"
  "--crypt-behavior" "2"
)

{
  printf 'ACTIVE_CONFIG_PATH=%s\n' "$ACTIVE_CONFIG_PATH"
  printf 'SYSTEM_NAME=%s\n' "$SYSTEM_NAME"
  printf 'CONTROL_FREQUENCY_HZ=%s\n' "$CONTROL_FREQUENCY_HZ"
  printf 'CONTROL_FREQUENCY_MHZ=%s\n' "$CONTROL_FREQUENCY_MHZ"
  printf 'P25_CONTROL_SERIAL=%s\n' "$P25_CONTROL_SERIAL"
  printf 'P25_CONTROL_GAIN_INT=%s\n' "$P25_CONTROL_GAIN_INT"
  printf 'P25_CONTROL_PPM=%s\n' "$P25_CONTROL_PPM"
  printf 'TRUNK_TSV=%s\n' "$TRUNK_TSV"
  printf 'RX_COMMAND='
  printf '%q ' "${RX_CMD[@]}"
  printf '\n'
} > "$COMMAND_FILE"
pass "wrote candidate command file: $COMMAND_FILE"

printf '\nCandidate rx.py command:\n' | tee -a "$REPORT_FILE"
printf '%q ' "${RX_CMD[@]}" | tee -a "$REPORT_FILE"
printf '\n' | tee -a "$REPORT_FILE"

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if [[ "$MODE" == "dry-run" ]]; then
  pass "dry-run selected; OP25 live command was not started"
elif [[ "$MODE" == "rx-smoke" ]]; then
  warn "starting bounded rx.py smoke run for ${SECONDS_LIMIT}s; no backend launch or service changes will be made"
  set +e
  timeout "${SECONDS_LIMIT}s" "${RX_CMD[@]}" > "$SMOKE_LOG" 2>&1
  rc=$?
  set -e
  if [[ "$rc" -eq 124 ]]; then
    if grep -Eiq 'Traceback|ImportError|ModuleNotFoundError|osmosdr source_c creation failure|No supported devices found|Failed to open|Exception' "$SMOKE_LOG"; then
      fail "rx.py reached timeout but smoke log contains startup/import/source errors; see $SMOKE_LOG"
    else
      pass "rx.py smoke stayed alive until bounded timeout (${SECONDS_LIMIT}s); see $SMOKE_LOG"
      cat > runtime/settings/op25_validated_rx_command.env <<ENV
# Generated by tools/pi5_p25_op25_live_command_probe.sh on ${STAMP}
# Evidence only. Backend live launch remains disabled until a later patch consumes this template.
P25_VALIDATED_RX_APP=$RX_PY
P25_VALIDATED_RX_ARGS=rtl=$P25_CONTROL_SERIAL
P25_VALIDATED_RX_SAMPLE_RATE=$SAMPLE_RATE
P25_VALIDATED_RX_GAIN=LNA:$P25_CONTROL_GAIN_INT
P25_VALIDATED_RX_PPM=$P25_CONTROL_PPM
P25_VALIDATED_RX_TRUNK_TSV=$TRUNK_TSV
P25_VALIDATED_RX_TERMINAL=$TERMINAL_TYPE
P25_VALIDATED_RX_CRYPT_BEHAVIOR=2
P25_VALIDATED_RX_SECONDS=$SECONDS_LIMIT
P25_VALIDATED_RX_REPORT=$REPORT_FILE
ENV
      pass "wrote validated command evidence marker: runtime/settings/op25_validated_rx_command.env"
    fi
  elif [[ "$rc" -eq 0 ]]; then
    pass "rx.py smoke exited cleanly rc=0; see $SMOKE_LOG"
  else
    fail "rx.py smoke exited early rc=$rc; see $SMOKE_LOG"
  fi
else
  fail "unknown mode: $MODE"
fi

printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'Command file: %s\n' "$COMMAND_FILE" | tee -a "$REPORT_FILE"
if [[ -f "$SMOKE_LOG" ]]; then
  printf 'Smoke log: %s\n' "$SMOKE_LOG" | tee -a "$REPORT_FILE"
fi
printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
