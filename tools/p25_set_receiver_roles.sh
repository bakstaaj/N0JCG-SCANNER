#!/usr/bin/env bash
# Set P25 RTL receiver role serials in the ignored local runtime config.
# Usage: ./tools/p25_set_receiver_roles.sh <control_serial> [voice_serial] [config_path]

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
pass() { printf 'PASS: %s\n' "$*"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

printf '=== PI-P25-SCANNER set receiver roles ===\n'

if [[ -f "DEV_GUARDRAILS.md" && -d "config" && -d "tools" ]]; then
  pass "running from repository root"
else
  fail "run from PI-P25-SCANNER repository root"
  printf 'FINAL: FAIL\n'
  exit 1
fi

CONTROL_SERIAL="${1:-}"
VOICE_SERIAL="${2:-}"
CONFIG_PATH="${3:-runtime/settings/p25_systems.json}"
TEMPLATE="config/p25_systems.local.example.json"

if [[ -z "$CONTROL_SERIAL" ]]; then
  fail "control serial is required"
  printf 'Usage: ./tools/p25_set_receiver_roles.sh <control_serial> [voice_serial] [config_path]\n'
  printf 'FINAL: FAIL\n'
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 missing"
  printf 'FINAL: FAIL\n'
  exit 1
fi

mkdir -p "$(dirname "$CONFIG_PATH")"
if [[ ! -f "$CONFIG_PATH" ]]; then
  if [[ -f "$TEMPLATE" ]]; then
    cp "$TEMPLATE" "$CONFIG_PATH"
    pass "created local config from template: $CONFIG_PATH"
  else
    fail "missing template: $TEMPLATE"
    printf 'FINAL: FAIL\n'
    exit 1
  fi
else
  pass "local config exists: $CONFIG_PATH"
fi

BACKUP="${CONFIG_PATH}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
cp "$CONFIG_PATH" "$BACKUP"
pass "backup created: $BACKUP"

if PYTHONPATH=src python3 - "$CONFIG_PATH" "$CONTROL_SERIAL" "$VOICE_SERIAL" <<'PY'
from __future__ import annotations
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
control_serial = sys.argv[2].strip()
voice_serial = sys.argv[3].strip()

payload = json.loads(path.read_text(encoding="utf-8"))
systems = payload.get("systems")
if not isinstance(systems, list) or not systems:
    raise SystemExit("CONFIG_ERROR: at least one system is required")

system = None
for item in systems:
    if isinstance(item, dict) and item.get("enabled", True):
        system = item
        break
if system is None:
    raise SystemExit("CONFIG_ERROR: no enabled system found")

roles = system.setdefault("receiver_roles", {})
control = roles.setdefault("p25_control", {})
voice = roles.setdefault("p25_voice", {})
control["rtl_serial"] = control_serial
if voice_serial:
    voice["rtl_serial"] = voice_serial

path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
print(f"CONFIG_UPDATED: {path}")
print(f"P25_CONTROL_SERIAL: {control_serial}")
print(f"P25_VOICE_SERIAL: {voice_serial or '(unchanged/blank)'}")
PY
then
  pass "updated receiver role serials"
else
  fail "failed to update receiver role serials"
fi

if [[ "$FAIL_COUNT" -eq 0 ]]; then
  if ./tools/p25_validate_config.sh "$CONFIG_PATH"; then
    pass "updated local config validates"
  else
    fail "updated local config validation failed"
  fi
fi

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n'
  exit 0
fi
printf 'FINAL: FAIL\n'
exit 1
