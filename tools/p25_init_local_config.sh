#!/usr/bin/env bash
# Initialize a runtime-local P25 scanner configuration from the local example.
# Run from the scanner repository root.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
pass() { printf 'PASS: %s\n' "$*"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

printf '=== scanner local config init ===\n'

if [[ -f "DEV_GUARDRAILS.md" && -d "config" && -d "runtime" || -f "DEV_GUARDRAILS.md" && -d "config" ]]; then
  pass "running from repository root"
else
  fail "run from scanner repository root"
  printf 'FINAL: FAIL\n'
  exit 1
fi

SRC="config/p25_systems.local.example.json"
DEST="runtime/settings/p25_systems.json"

if [[ ! -f "$SRC" ]]; then
  fail "missing template: $SRC"
  printf 'FINAL: FAIL\n'
  exit 1
fi

mkdir -p "$(dirname "$DEST")"

if [[ -f "$DEST" ]]; then
  warn "local config already exists; leaving unchanged: $DEST"
else
  cp "$SRC" "$DEST"
  pass "created local config: $DEST"
fi

if command -v python3 >/dev/null 2>&1; then
  if python3 -m json.tool "$DEST" >/dev/null; then
    pass "local config JSON validates"
  else
    fail "local config JSON invalid: $DEST"
  fi
else
  fail "python3 missing"
fi

printf 'Config path: %s\n' "$DEST"
printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n'
  exit 0
fi
printf 'FINAL: FAIL\n'
exit 1
