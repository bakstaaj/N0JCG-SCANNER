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
META_ENV="$REPORT_DIR/op25_command_meta_${STAMP}.env"
HELP_LOG="$REPORT_DIR/op25_rx_help_${STAMP}.txt"
SOURCE_OPT_LOG="$REPORT_DIR/op25_rx_source_options_${STAMP}.txt"
MODE="dry-run"
SECONDS_LIMIT=20
YES=0
TERMINAL_TYPE="http:127.0.0.1:18091"
SAMPLE_RATE=960000
APP="rx"
PROJECT_ROOT="$(pwd -P)"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

usage() {
  cat <<USAGE
Usage:
  ./tools/pi5_p25_op25_live_command_probe.sh --dry-run
  ./tools/pi5_p25_op25_live_command_probe.sh --rx-smoke --seconds 20 --yes

Options:
  --dry-run          Generate and print candidate rx.py commands only. Default.
  --rx-smoke         Run bounded rx.py foreground smoke tests with timeout.
  --seconds N        Smoke-test duration. Default: 20. Allowed: 5-120.
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

for required_cmd in python3 timeout; do
  if command -v "$required_cmd" >/dev/null 2>&1; then
    pass "command available: $required_cmd"
  else
    fail "missing required command: $required_cmd"
  fi
done

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
APP_DIR="$SOURCE_DIR/op25/gr-op25_repeater/apps"
TDMA_DIR="$APP_DIR/tdma"
TX_DIR="$APP_DIR/tx"
RX_PY="$APP_DIR/rx.py"
MULTI_RX_PY="$APP_DIR/multi_rx.py"
ORIGINAL_PYTHONPATH="${PYTHONPATH:-}"
if [[ -n "$ORIGINAL_PYTHONPATH" ]]; then
  OP25_PYTHONPATH="$APP_DIR:$TDMA_DIR:$TX_DIR:$ORIGINAL_PYTHONPATH"
else
  OP25_PYTHONPATH="$APP_DIR:$TDMA_DIR:$TX_DIR"
fi

if [[ -d "$SOURCE_DIR" ]]; then
  pass "OP25 source directory exists: $SOURCE_DIR"
else
  fail "OP25 source directory missing: $SOURCE_DIR"
fi

if [[ -d "$APP_DIR" ]]; then
  pass "OP25 apps directory exists: $APP_DIR"
else
  fail "OP25 apps directory missing: $APP_DIR"
fi

if [[ -d "$TDMA_DIR" ]]; then
  pass "OP25 TDMA import directory exists: $TDMA_DIR"
else
  fail "OP25 TDMA import directory missing: $TDMA_DIR"
fi

if [[ -d "$TX_DIR" ]]; then
  pass "OP25 TX import directory exists: $TX_DIR"
else
  fail "OP25 TX import directory missing: $TX_DIR"
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

TRUNK_TSV="$PROJECT_ROOT/runtime/op25/trunk.tsv"
if [[ -f "$TRUNK_TSV" ]]; then
  pass "generated trunk TSV exists: $TRUNK_TSV"
else
  fail "generated trunk TSV missing: $TRUNK_TSV"
fi

if PYTHONPATH=src python3 - "$META_ENV" "$PROJECT_ROOT" <<'PY_META'
from __future__ import annotations
import shlex
import sys
from pathlib import Path
from pi_p25_scanner.config_store import load_active_project_config
from pi_p25_scanner.config_model import hz_to_mhz_string
out = Path(sys.argv[1])
root = Path(sys.argv[2])
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
    "TRUNK_TSV": str(root / "runtime" / "op25" / "trunk.tsv"),
}
out.write_text("".join(f"{key}={shlex.quote(value)}\n" for key, value in lines.items()), encoding="utf-8")
PY_META
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

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

validate_source_options() {
  python3 - "$RX_PY" "$SOURCE_OPT_LOG" <<'PY_OPTS'
from __future__ import annotations
import re
import sys
from pathlib import Path
rx_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
text = rx_path.read_text(encoding="utf-8", errors="replace")
checks = {
    "--args": r"--args",
    "-S": r"['\"]-S['\"]",
    "-q": r"['\"]-q['\"]",
    "-N": r"['\"]-N['\"]",
    "-T": r"['\"]-T['\"]",
    "-V": r"['\"]-V['\"]",
    "-U": r"['\"]-U['\"]",
    "-l": r"['\"]-l['\"]",
    "--crypt-behavior": r"--crypt-behavior",
    "-2": r"['\"]-2['\"]",
}
results: list[str] = []
missing: list[str] = []
for opt, pattern in checks.items():
    found = re.search(pattern, text) is not None
    results.append(f"{opt}={'present' if found else 'missing'}")
    if not found:
        missing.append(opt)
out_path.write_text("\n".join(results) + "\n", encoding="utf-8")
if missing:
    print("MISSING=" + ",".join(missing))
    raise SystemExit(1)
print("SOURCE_OPTION_VALIDATION_PASS")
PY_OPTS
}

set +e
(
  cd "$APP_DIR"
  env PYTHONPATH="$OP25_PYTHONPATH" timeout 10s "$RX_PY" --help
) > "$HELP_LOG" 2>&1
HELP_RC=$?
set -e
if [[ "$HELP_RC" -eq 0 ]]; then
  pass "rx.py help completed with OP25 app cwd/import path"
else
  warn "rx.py --help returned rc=$HELP_RC; source option validation will be used; see $HELP_LOG"
fi

if validate_source_options >> "$REPORT_FILE" 2>&1; then
  pass "rx.py source includes expected option definitions"
else
  fail "rx.py source option validation failed; see $SOURCE_OPT_LOG"
fi

HAS_PHASE2=0
HAS_CRYPT=0
HAS_TERMINAL=0
if grep -q -- '^-2=present$' "$SOURCE_OPT_LOG"; then
  HAS_PHASE2=1
  pass "rx.py source includes Phase II option: -2"
else
  warn "rx.py source does not include -2; Phase II flag will be omitted"
fi
if grep -q -- '^--crypt-behavior=present$' "$SOURCE_OPT_LOG"; then
  HAS_CRYPT=1
  pass "rx.py source includes encrypted-call behavior option"
else
  warn "rx.py source does not include --crypt-behavior; encrypted-call flag will be omitted"
fi
if grep -q -- '^-l=present$' "$SOURCE_OPT_LOG"; then
  HAS_TERMINAL=1
  pass "rx.py source includes terminal option: -l"
else
  warn "rx.py source does not include -l; terminal flag will be omitted"
fi

find_rtl_index_for_serial() {
  local serial="$1"
  local idx out parsed
  if ! command -v rtl_eeprom >/dev/null 2>&1; then
    return 1
  fi
  for idx in 0 1 2 3 4 5 6 7; do
    out="$REPORT_DIR/rtl_eeprom_index_${idx}_${STAMP}.txt"
    if timeout 6s rtl_eeprom -d "$idx" > "$out" 2>&1; then
      parsed="$(awk -F: '/Serial number/{gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit}' "$out" || true)"
      if [[ "$parsed" == "$serial" ]]; then
        printf '%s\n' "$idx"
        return 0
      fi
    fi
  done
  return 1
}

P25_CONTROL_INDEX=""
if P25_CONTROL_INDEX="$(find_rtl_index_for_serial "$P25_CONTROL_SERIAL")"; then
  pass "p25_control runtime index detected: $P25_CONTROL_INDEX"
else
  warn "could not map p25_control serial to a runtime RTL index; serial-only candidate will be used"
  P25_CONTROL_INDEX=""
fi

build_rx_cmd() {
  local device_arg="$1"
  RX_CMD=(
    "$RX_PY"
    "--args" "$device_arg"
    "-S" "$SAMPLE_RATE"
    "-q" "$P25_CONTROL_PPM"
    "-N" "LNA:$P25_CONTROL_GAIN_INT"
    "-T" "$TRUNK_TSV"
    "-V"
    "-U"
  )
  if [[ "$HAS_PHASE2" -eq 1 ]]; then
    RX_CMD+=("-2")
  fi
  if [[ "$HAS_TERMINAL" -eq 1 ]]; then
    RX_CMD+=("-l" "$TERMINAL_TYPE")
  fi
  if [[ "$HAS_CRYPT" -eq 1 ]]; then
    RX_CMD+=("--crypt-behavior" "2")
  fi
}

classify_smoke_log() {
  local log="$1"
  if grep -Eiq 'ModuleNotFoundError|ImportError|No module named' "$log"; then
    printf 'IMPORT_ERROR'
  elif grep -Eiq 'no such option|unrecognized arguments|option .*not recognized|Usage:.*rx\.py' "$log"; then
    printf 'OPTION_ERROR'
  elif grep -Eiq 'No supported devices found|Failed to open|unable to open|usb_claim_interface|LIBUSB_ERROR|source_c creation failure|osmosdr.*source|rtl.*open|No such device|device.*busy|Found 0 device' "$log"; then
    printf 'SDR_OPEN_ERROR'
  elif grep -Eiq 'trunk.*No such file|No such file.*trunk|cannot open.*trunk|failed to open.*tsv' "$log"; then
    printf 'CONFIG_FILE_ERROR'
  elif grep -Eiq 'Traceback|Exception|RuntimeError|ValueError' "$log"; then
    printf 'PYTHON_RUNTIME_ERROR'
  else
    printf 'UNKNOWN_EARLY_EXIT'
  fi
}

write_validated_marker() {
  local label="$1"
  local device_arg="$2"
  local log="$3"
  cat > runtime/settings/op25_validated_rx_command.env <<ENV
# Generated by tools/pi5_p25_op25_live_command_probe.sh on ${STAMP}
# Evidence only. Backend live launch remains disabled until a later patch consumes this template.
P25_VALIDATED_RX_APP=$RX_PY
P25_VALIDATED_RX_APP_DIR=$APP_DIR
P25_VALIDATED_RX_PYTHONPATH=$OP25_PYTHONPATH
P25_VALIDATED_RX_DEVICE_LABEL=$label
P25_VALIDATED_RX_ARGS=$device_arg
P25_VALIDATED_RX_SAMPLE_RATE=$SAMPLE_RATE
P25_VALIDATED_RX_GAIN=LNA:$P25_CONTROL_GAIN_INT
P25_VALIDATED_RX_PPM=$P25_CONTROL_PPM
P25_VALIDATED_RX_TRUNK_TSV=$TRUNK_TSV
P25_VALIDATED_RX_TERMINAL=$TERMINAL_TYPE
P25_VALIDATED_RX_CRYPT_BEHAVIOR=2
P25_VALIDATED_RX_SECONDS=$SECONDS_LIMIT
P25_VALIDATED_RX_REPORT=$REPORT_FILE
P25_VALIDATED_RX_LOG=$log
ENV
  pass "wrote validated command evidence marker: runtime/settings/op25_validated_rx_command.env"
}

write_command_file() {
  : > "$COMMAND_FILE"
  {
    printf 'ACTIVE_CONFIG_PATH=%s\n' "$ACTIVE_CONFIG_PATH"
    printf 'SYSTEM_NAME=%s\n' "$SYSTEM_NAME"
    printf 'CONTROL_FREQUENCY_HZ=%s\n' "$CONTROL_FREQUENCY_HZ"
    printf 'CONTROL_FREQUENCY_MHZ=%s\n' "$CONTROL_FREQUENCY_MHZ"
    printf 'P25_CONTROL_SERIAL=%s\n' "$P25_CONTROL_SERIAL"
    printf 'P25_CONTROL_INDEX=%s\n' "$P25_CONTROL_INDEX"
    printf 'P25_CONTROL_GAIN_INT=%s\n' "$P25_CONTROL_GAIN_INT"
    printf 'P25_CONTROL_PPM=%s\n' "$P25_CONTROL_PPM"
    printf 'TRUNK_TSV=%s\n' "$TRUNK_TSV"
    printf 'OP25_APP_DIR=%s\n' "$APP_DIR"
    printf 'OP25_PYTHONPATH=%s\n' "$OP25_PYTHONPATH"
  } >> "$COMMAND_FILE"
  build_rx_cmd "rtl=$P25_CONTROL_SERIAL"
  printf 'RX_COMMAND_SERIAL_CD=%q ' cd "$APP_DIR" >> "$COMMAND_FILE"
  printf '&& PYTHONPATH=%q ' "$OP25_PYTHONPATH" >> "$COMMAND_FILE"
  printf '%q ' "${RX_CMD[@]}" >> "$COMMAND_FILE"
  printf '\n' >> "$COMMAND_FILE"
  if [[ -n "$P25_CONTROL_INDEX" ]]; then
    build_rx_cmd "rtl=$P25_CONTROL_INDEX"
    printf 'RX_COMMAND_INDEX_CD=%q ' cd "$APP_DIR" >> "$COMMAND_FILE"
    printf '&& PYTHONPATH=%q ' "$OP25_PYTHONPATH" >> "$COMMAND_FILE"
    printf '%q ' "${RX_CMD[@]}" >> "$COMMAND_FILE"
    printf '\n' >> "$COMMAND_FILE"
  fi
  pass "wrote candidate command file: $COMMAND_FILE"
}

print_candidate() {
  local label="$1"
  local device_arg="$2"
  build_rx_cmd "$device_arg"
  printf '\nCandidate rx.py command (%s):\n' "$label" | tee -a "$REPORT_FILE"
  printf 'cd %q && PYTHONPATH=%q ' "$APP_DIR" "$OP25_PYTHONPATH" | tee -a "$REPORT_FILE"
  printf '%q ' "${RX_CMD[@]}" | tee -a "$REPORT_FILE"
  printf '\n' | tee -a "$REPORT_FILE"
}

run_smoke_candidate() {
  local label="$1"
  local device_arg="$2"
  local log="$REPORT_DIR/op25_rx_smoke_${label}_${STAMP}.log"
  local class rc
  build_rx_cmd "$device_arg"
  warn "starting bounded rx.py smoke run for ${SECONDS_LIMIT}s using ${label}; no backend launch or service changes will be made"
  set +e
  (
    cd "$APP_DIR"
    env PYTHONPATH="$OP25_PYTHONPATH" timeout "${SECONDS_LIMIT}s" "${RX_CMD[@]}"
  ) > "$log" 2>&1
  rc=$?
  set -e
  if [[ "$rc" -eq 124 ]]; then
    if grep -Eiq 'Traceback|ImportError|ModuleNotFoundError|source_c creation failure|No supported devices found|Failed to open|Exception|RuntimeError|ValueError' "$log"; then
      class="$(classify_smoke_log "$log")"
      warn "rx.py reached timeout but smoke log contains startup/runtime markers; classification=$class; see $log"
      printf '%s\n' "--- smoke log tail ($label) ---" | tee -a "$REPORT_FILE"
      tail -n 80 "$log" | tee -a "$REPORT_FILE" || true
      return 1
    fi
    pass "rx.py smoke stayed alive until bounded timeout (${SECONDS_LIMIT}s) using ${label}; see $log"
    write_validated_marker "$label" "$device_arg" "$log"
    return 0
  fi
  if [[ "$rc" -eq 0 ]]; then
    pass "rx.py smoke exited cleanly rc=0 using ${label}; see $log"
    write_validated_marker "$label" "$device_arg" "$log"
    return 0
  fi
  class="$(classify_smoke_log "$log")"
  warn "rx.py smoke candidate ${label} exited early rc=$rc classification=$class; see $log"
  printf '%s\n' "--- smoke log tail ($label) ---" | tee -a "$REPORT_FILE"
  tail -n 80 "$log" | tee -a "$REPORT_FILE" || true
  printf 'LAST_SMOKE_CLASSIFICATION=%s\nLAST_SMOKE_RC=%s\nLAST_SMOKE_LOG=%s\n' "$class" "$rc" "$log" > runtime/settings/op25_live_command_last_failure.env
  return 1
}

write_command_file
print_candidate "serial" "rtl=$P25_CONTROL_SERIAL"
if [[ -n "$P25_CONTROL_INDEX" ]]; then
  print_candidate "runtime-index" "rtl=$P25_CONTROL_INDEX"
fi

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if [[ "$MODE" == "dry-run" ]]; then
  pass "dry-run selected; OP25 live command was not started"
elif [[ "$MODE" == "rx-smoke" ]]; then
  SMOKE_PASS=0
  if run_smoke_candidate "serial" "rtl=$P25_CONTROL_SERIAL"; then
    SMOKE_PASS=1
  elif [[ -n "$P25_CONTROL_INDEX" ]]; then
    warn "serial candidate did not validate; trying runtime index candidate"
    if run_smoke_candidate "runtime_index" "rtl=$P25_CONTROL_INDEX"; then
      SMOKE_PASS=1
    fi
  fi
  if [[ "$SMOKE_PASS" -ne 1 ]]; then
    fail "no rx.py smoke candidate validated; see report and smoke logs"
  fi
else
  fail "unknown mode: $MODE"
fi

printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'Command file: %s\n' "$COMMAND_FILE" | tee -a "$REPORT_FILE"
printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
