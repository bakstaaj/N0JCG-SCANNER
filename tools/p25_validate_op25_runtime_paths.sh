#!/usr/bin/env bash
# Validate generated OP25 trunk TSV file references are absolute and readable.
# Run from scanner repository root.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_DIR=".p25_op25_runtime_path_validation_reports"
REPORT_FILE="$REPORT_DIR/op25_runtime_paths_${STAMP}.txt"
OUTPUT_DIR="$REPORT_DIR/generated_op25_${STAMP}"
MANIFEST="$REPORT_DIR/manifest_${STAMP}.json"

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); }

printf '=== scanner OP25 runtime path validation ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" ]]; then
  pass "running from repository root"
else
  fail "run from scanner repository root"
fi

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
fi

CONFIG_PATH="${P25_SCANNER_CONFIG:-config/p25_systems.example.json}"
if [[ -f "$CONFIG_PATH" ]]; then
  pass "config file exists: $CONFIG_PATH"
else
  fail "config file missing: $CONFIG_PATH"
fi

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if PYTHONPATH=src python3 -m pi_p25_scanner.op25_config --config "$CONFIG_PATH" --output "$OUTPUT_DIR" --json > "$MANIFEST"; then
    pass "generated OP25 runtime config for path validation"
  else
    fail "OP25 runtime config generation failed"
  fi
fi

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if python3 - "$MANIFEST" <<'PY_VALIDATE' | tee -a "$REPORT_FILE"; then
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
trunk = Path(manifest["trunk_tsv"])
if not trunk.is_absolute():
    raise SystemExit(f"trunk_tsv is not absolute in manifest: {trunk}")
if not trunk.exists():
    raise SystemExit(f"trunk_tsv does not exist: {trunk}")

with trunk.open("r", encoding="utf-8", newline="") as handle:
    rows = list(csv.DictReader(handle, delimiter="\t"))

if not rows:
    raise SystemExit("generated trunk.tsv has no system rows")

for row in rows:
    sysname = row.get("Sysname", "<unknown>")
    for column in ("TGID Tags File", "Whitelist", "Blacklist"):
        value = row.get(column, "")
        path = Path(value)
        if not path.is_absolute():
            raise SystemExit(f"{sysname}: {column} is not absolute: {value}")
        if not path.exists():
            raise SystemExit(f"{sysname}: {column} does not exist: {value}")
        print(f"PASS_PATH: {sysname}: {column}: {path}")

for system in manifest.get("systems", []):
    for key in ("tags_file", "whitelist_file", "blacklist_file"):
        value = system.get(key, "")
        path = Path(value)
        if not path.is_absolute():
            raise SystemExit(f"manifest {key} is not absolute: {value}")
        if not path.exists():
            raise SystemExit(f"manifest {key} does not exist: {value}")
print("OP25_RUNTIME_PATH_VALIDATION_PASS")
PY_VALIDATE
    pass "generated OP25 paths are absolute and readable"
  else
    fail "generated OP25 path validation failed"
  fi
fi

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
