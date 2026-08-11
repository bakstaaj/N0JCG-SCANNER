#!/usr/bin/env bash
# Generate non-invasive OP25 command-path evidence for scanner.
# This does not launch OP25 as a decoder.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_op25_command_candidate_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/op25_command_candidate_${STAMP}.txt"
OUTPUT_JSON="runtime/settings/op25_command_candidate.json"
SOURCE_ENV="runtime/settings/op25_source_path.env"
SOURCE_DIR="${OP25_SOURCE_DIR:-$HOME/op25}"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

mkdir -p "$REPORT_DIR" runtime/settings
: > "$REPORT_FILE"
printf '=== scanner OP25 command candidate probe ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "tools" && -d "src/pi_p25_scanner" ]]; then
  pass "running from repository root"
else
  fail "run from scanner repository root"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if [[ -f "$SOURCE_ENV" ]]; then
  # shellcheck disable=SC1090
  source "$SOURCE_ENV"
  SOURCE_DIR="${OP25_SOURCE_DIR:-$SOURCE_DIR}"
  pass "loaded OP25 source marker: $SOURCE_ENV"
else
  warn "OP25 source marker missing; using default source dir: $SOURCE_DIR"
fi

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
fi

if [[ -d "$SOURCE_DIR" ]]; then
  pass "OP25 source directory exists: $SOURCE_DIR"
else
  fail "OP25 source directory missing: $SOURCE_DIR"
fi

APPS_DIR="$SOURCE_DIR/op25/gr-op25_repeater/apps"
RX_PY="$APPS_DIR/rx.py"
MULTI_RX_PY="$APPS_DIR/multi_rx.py"

for file in "$RX_PY" "$MULTI_RX_PY"; do
  if [[ -f "$file" ]]; then
    pass "OP25 app file exists: $file"
    if python3 -m py_compile "$file" >> "$REPORT_FILE" 2>&1; then
      pass "python syntax compiles: $file"
    else
      warn "python syntax/dependency compile check failed: $file"
    fi
  else
    warn "OP25 app file missing: $file"
  fi
done

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if ./tools/p25_generate_op25_config.sh >> "$REPORT_FILE" 2>&1; then
    pass "generated PI-P25 OP25 runtime config"
  else
    fail "PI-P25 OP25 runtime config generation failed"
  fi
fi

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if PYTHONPATH=src python3 - "$SOURCE_DIR" "$OUTPUT_JSON" <<'PY'
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

from pi_p25_scanner.config_store import load_active_project_config
from pi_p25_scanner.config_model import hz_to_mhz_string

source_dir = Path(sys.argv[1]).expanduser().resolve()
out_path = Path(sys.argv[2])
apps_dir = source_dir / "op25" / "gr-op25_repeater" / "apps"
rx_py = apps_dir / "rx.py"
multi_rx_py = apps_dir / "multi_rx.py"
config, config_path = load_active_project_config()
system = config.first_enabled_system()
control_role = system.receiver_roles.get("p25_control")
voice_role = system.receiver_roles.get("p25_voice")
control_freq = system.control_channels_hz[0]

payload = {
    "schema_version": 1,
    "source": "tools/pi5_p25_op25_command_candidate.sh",
    "source_dir": str(source_dir),
    "apps_dir": str(apps_dir),
    "rx_py": str(rx_py),
    "rx_py_exists": rx_py.exists(),
    "multi_rx_py": str(multi_rx_py),
    "multi_rx_py_exists": multi_rx_py.exists(),
    "active_config_path": str(config_path),
    "system_name": system.name,
    "control_frequency_hz": control_freq,
    "control_frequency_mhz": hz_to_mhz_string(control_freq),
    "p25_control_serial": control_role.rtl_serial if control_role else "",
    "p25_voice_serial": voice_role.rtl_serial if voice_role else "",
    "generated_trunk_tsv": "runtime/op25/trunk.tsv",
    "candidate_status": "source path evidence only; command template not validated for live backend launch",
    "live_launch_enabled": False,
    "notes": [
        "Do not enable backend live OP25 launch from this file alone.",
        "Validate exact rx.py or multi_rx.py command manually on the Pi first.",
        "Encrypted calls remain mute/log only in scanner scope.",
    ],
}
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
PY
  then
    pass "wrote OP25 command candidate JSON: $OUTPUT_JSON"
  else
    fail "failed to write OP25 command candidate JSON"
  fi
fi

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'Candidate JSON: %s\n' "$OUTPUT_JSON" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
