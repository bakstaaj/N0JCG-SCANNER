#!/usr/bin/env bash
# Validate OP25 post-install command evidence without launching live decode.
# Run from the PI-P25-SCANNER repository root on Raspberry Pi.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_op25_postinstall_probe_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/op25_postinstall_probe_${STAMP}.txt"
JSON_OUT="runtime/settings/op25_postinstall_probe.json"
HELP_DIR="$REPORT_DIR/help_${STAMP}"
IMPORT_LOG="$REPORT_DIR/python_imports_${STAMP}.txt"
CANDIDATE_JSON="runtime/settings/op25_command_candidate.json"
SOURCE_MARKER="runtime/settings/op25_source_path.env"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

mkdir -p "$REPORT_DIR" "$HELP_DIR" runtime/settings runtime/op25
: > "$REPORT_FILE"
printf '=== PI-P25-SCANNER OP25 post-install probe ===\n' | tee -a "$REPORT_FILE"

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
  fail "target runtime must be Linux/Raspberry Pi"
fi

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
fi

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if ./tools/p25_validate_config.sh >> "$REPORT_FILE" 2>&1; then
    pass "active PI-P25 config validates"
  else
    fail "active PI-P25 config validation failed; see $REPORT_FILE"
  fi
fi

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if PYTHONPATH=src python3 -m pi_p25_scanner.op25_config --output runtime/op25 --json > "$REPORT_DIR/generated_manifest_${STAMP}.json"; then
    pass "generated PI-P25 OP25 runtime files"
  else
    fail "failed to generate PI-P25 OP25 runtime files"
  fi
fi

SOURCE_DIR="${OP25_SOURCE_DIR:-/home/pi/op25}"
if [[ -f "$SOURCE_MARKER" ]]; then
  # shellcheck disable=SC1090
  source "$SOURCE_MARKER"
  SOURCE_DIR="${OP25_SOURCE_DIR:-$SOURCE_DIR}"
  pass "loaded OP25 source marker: $SOURCE_MARKER"
else
  warn "OP25 source marker missing; using default source dir: $SOURCE_DIR"
fi

APPS_DIR="$SOURCE_DIR/op25/gr-op25_repeater/apps"
RX_PY="$APPS_DIR/rx.py"
MULTI_RX_PY="$APPS_DIR/multi_rx.py"

if [[ -d "$SOURCE_DIR" ]]; then
  pass "OP25 source directory exists: $SOURCE_DIR"
else
  fail "OP25 source directory missing: $SOURCE_DIR"
fi

for app in "$RX_PY" "$MULTI_RX_PY"; do
  if [[ -f "$app" ]]; then
    pass "OP25 app exists: $app"
    if python3 -m py_compile "$app" >> "$REPORT_FILE" 2>&1; then
      pass "python syntax compiles: $app"
    else
      fail "python syntax compile failed: $app"
    fi
  else
    fail "OP25 app missing: $app"
  fi
done

for command_name in rx.py multi_rx.py op25_rx.py; do
  if command -v "$command_name" >/dev/null 2>&1; then
    pass "installed command discoverable: $command_name -> $(command -v "$command_name")"
  else
    warn "installed command not on PATH: $command_name"
  fi
done

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if python3 - <<'PY' > "$IMPORT_LOG" 2>&1
from __future__ import annotations
modules = [
    "gnuradio",
    "gnuradio.blocks",
    "gnuradio.audio",
    "gnuradio.filter",
    "osmosdr",
]
failed = []
for name in modules:
    try:
        __import__(name)
        print(f"IMPORT_PASS {name}")
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        print(f"IMPORT_FAIL {name}: {exc}")
        failed.append(name)
raise SystemExit(0 if not failed else 1)
PY
  then
    pass "OP25/GNU Radio Python imports passed"
  else
    warn "one or more OP25/GNU Radio Python imports failed; see $IMPORT_LOG"
  fi
fi

run_help_probe() {
  local label="$1"
  local script_path="$2"
  local log_path="$HELP_DIR/${label}_help.txt"
  if [[ ! -f "$script_path" ]]; then
    warn "help probe skipped; missing $script_path"
    return 0
  fi
  if timeout 12s python3 "$script_path" --help > "$log_path" 2>&1; then
    pass "$label --help completed"
  else
    local rc=$?
    if [[ "$rc" -eq 124 ]]; then
      warn "$label --help timed out; see $log_path"
    else
      warn "$label --help returned rc=$rc; see $log_path"
    fi
  fi
}

run_help_probe "rx_py" "$RX_PY"
run_help_probe "multi_rx_py" "$MULTI_RX_PY"

if [[ -f "$CANDIDATE_JSON" ]]; then
  pass "candidate JSON exists: $CANDIDATE_JSON"
else
  warn "candidate JSON missing; run tools/pi5_p25_op25_command_candidate.sh after this probe"
fi

if command -v python3 >/dev/null 2>&1; then
  if python3 - "$JSON_OUT" "$SOURCE_DIR" "$RX_PY" "$MULTI_RX_PY" "$CANDIDATE_JSON" "$IMPORT_LOG" "$HELP_DIR" <<'PY'
from __future__ import annotations
import json
import sys
from pathlib import Path

out_path = Path(sys.argv[1])
source_dir = Path(sys.argv[2])
rx_py = Path(sys.argv[3])
multi_rx_py = Path(sys.argv[4])
candidate_json = Path(sys.argv[5])
import_log = Path(sys.argv[6])
help_dir = Path(sys.argv[7])

def maybe_read(path: Path, limit: int = 6000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit]

payload = {
    "schema_version": 1,
    "source": "tools/pi5_p25_op25_postinstall_probe.sh",
    "source_dir": str(source_dir),
    "rx_py": str(rx_py),
    "rx_py_exists": rx_py.exists(),
    "multi_rx_py": str(multi_rx_py),
    "multi_rx_py_exists": multi_rx_py.exists(),
    "candidate_json": str(candidate_json),
    "candidate_json_exists": candidate_json.exists(),
    "import_log": str(import_log),
    "help_dir": str(help_dir),
    "rx_help_excerpt": maybe_read(help_dir / "rx_py_help.txt"),
    "multi_rx_help_excerpt": maybe_read(help_dir / "multi_rx_py_help.txt"),
    "live_launch_enabled": False,
    "next_step": "Build and run a bounded manual control-channel validation command on the Pi before enabling backend live launch.",
}
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
PY
  then
    pass "wrote OP25 post-install probe JSON: $JSON_OUT"
  else
    fail "failed to write OP25 post-install probe JSON"
  fi
fi

printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
printf 'Post-install JSON: %s\n' "$JSON_OUT" | tee -a "$REPORT_FILE"
printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
