#!/usr/bin/env bash
# Generate OP25 runtime files from PI P25 Scanner JSON config.
# Run from the PI-P25-SCANNER repository root.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_generate_op25_reports"
REPORT_FILE="$REPORT_DIR/p25_generate_op25_$(date -u +%Y%m%dT%H%M%SZ).txt"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"

printf '=== PI-P25-SCANNER OP25 config generation ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -f "config/p25_systems.example.json" ]]; then
  pass "running from repository root"
else
  fail "run this script from the PI-P25-SCANNER repository root"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
fi

OUTPUT_DIR="${1:-runtime/op25}"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if PYTHONPATH=src python3 -m pi_p25_scanner.op25_config --config config/p25_systems.example.json --output "$OUTPUT_DIR" --json > "$REPORT_DIR/op25_manifest.json"; then
    pass "generated OP25 runtime files under $OUTPUT_DIR"
  else
    fail "OP25 config generation failed"
  fi
fi

for generated in "$OUTPUT_DIR/trunk.tsv" "$OUTPUT_DIR/manifest.json"; do
  if [[ -f "$generated" ]]; then
    pass "generated file exists: $generated"
  else
    fail "missing generated file: $generated"
  fi
done

if [[ -f "$OUTPUT_DIR/manifest.json" ]]; then
  if python3 -m json.tool "$OUTPUT_DIR/manifest.json" >/dev/null; then
    pass "generated manifest JSON validates"
  else
    fail "generated manifest JSON invalid"
  fi
fi

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
printf 'Report: %s\n' "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1
