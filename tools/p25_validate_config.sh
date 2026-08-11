#!/usr/bin/env bash
# Validate a scanner config JSON file.
# Default path: runtime/settings/p25_systems.json when present, otherwise config/p25_systems.example.json.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
pass() { printf 'PASS: %s\n' "$*"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

printf '=== scanner config validation ===\n'

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" ]]; then
  pass "running from repository root"
else
  fail "run from scanner repository root"
  printf 'FINAL: FAIL\n'
  exit 1
fi

CONFIG_PATH="${1:-}"
if [[ -z "$CONFIG_PATH" ]]; then
  if [[ -f "runtime/settings/p25_systems.json" ]]; then
    CONFIG_PATH="runtime/settings/p25_systems.json"
  else
    CONFIG_PATH="config/p25_systems.example.json"
    warn "runtime config missing; validating source example instead"
  fi
fi

if [[ -f "$CONFIG_PATH" ]]; then
  pass "config file exists: $CONFIG_PATH"
else
  fail "config file missing: $CONFIG_PATH"
  printf 'FINAL: FAIL\n'
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 missing"
  printf 'FINAL: FAIL\n'
  exit 1
fi

if python3 -m json.tool "$CONFIG_PATH" >/dev/null; then
  pass "JSON syntax validates"
else
  fail "JSON syntax invalid"
fi

if PYTHONPATH=src python3 - "$CONFIG_PATH" <<'PY'
from __future__ import annotations
import sys
from pathlib import Path
from pi_p25_scanner.config_model import ConfigError, load_project_config
path = Path(sys.argv[1])
try:
    cfg = load_project_config(path)
    system = cfg.first_enabled_system()
except ConfigError as exc:
    print(f"CONFIG_ERROR: {exc}")
    raise SystemExit(1)
print(f"SYSTEM: {system.name}")
print(f"CONTROL_CHANNELS: {len(system.control_channels_hz)}")
print(f"TALKGROUPS: {len(system.talkgroups)}")
print(f"ROLES: {','.join(sorted(system.receiver_roles.keys()))}")
PY
then
  pass "project config model validates"
else
  fail "project config model validation failed"
fi

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n'
  exit 0
fi
printf 'FINAL: FAIL\n'
exit 1
