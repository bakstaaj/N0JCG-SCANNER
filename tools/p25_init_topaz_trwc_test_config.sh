#!/usr/bin/env bash
# Initialize ignored runtime config from the checked-in TOPAZ/TRWC Mesa test profile.
# Default is dry-run. Use --apply --yes to write runtime/settings/p25_systems.json.
set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
MODE="dry-run"
YES=0
TEMPLATE="config/topaz_trwc_mesa_test.json"
RUNTIME_CONFIG="runtime/settings/p25_systems.json"
REPORT_DIR=".p25_topaz_trwc_profile_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/topaz_trwc_init_${STAMP}.txt"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }
finish() {
  printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
    exit 0
  fi
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
}
usage() {
  cat <<USAGE
Usage:
  ./tools/p25_init_topaz_trwc_test_config.sh --dry-run
  ./tools/p25_init_topaz_trwc_test_config.sh --apply --yes

Creates runtime/settings/p25_systems.json from config/topaz_trwc_mesa_test.json.
Existing RTL serial/gain/ppm receiver roles are preserved when possible.
USAGE
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) MODE="dry-run"; shift ;;
    --apply) MODE="apply"; shift ;;
    --yes) YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage; exit 1 ;;
  esac
done

mkdir -p "$REPORT_DIR" runtime/settings runtime/op25
: > "$REPORT_FILE"
printf '=== scanner TOPAZ/TRWC runtime config initializer ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "src/pi_p25_scanner" && -d "tools" ]]; then
  pass "running from repository root"
else
  fail "run from scanner repository root"
  finish
fi

if [[ -f "$TEMPLATE" ]]; then
  pass "TOPAZ/TRWC template exists: $TEMPLATE"
else
  fail "missing TOPAZ/TRWC template: $TEMPLATE"
fi

if [[ "$MODE" == "apply" && "$YES" -ne 1 ]]; then
  fail "--apply requires --yes"
fi

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  finish
fi

if PYTHONPATH=src python3 - "$TEMPLATE" "$RUNTIME_CONFIG" "$MODE" <<'PY_INIT' | tee -a "$REPORT_FILE"
from __future__ import annotations
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from pi_p25_scanner.config_store import validate_config_payload

template_path = Path(sys.argv[1])
runtime_path = Path(sys.argv[2])
mode = sys.argv[3]

template = json.loads(template_path.read_text(encoding="utf-8"))
validate_config_payload(template)
merged = deepcopy(template)

existing_roles = None
if runtime_path.exists():
    try:
        existing = json.loads(runtime_path.read_text(encoding="utf-8"))
        validate_config_payload(existing)
        existing_system = existing.get("systems", [{}])[0]
        existing_roles = existing_system.get("receiver_roles")
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        print(f"WARN: existing runtime config could not be parsed for role preservation: {exc}")

if existing_roles:
    merged["systems"][0]["receiver_roles"] = existing_roles
    print("PASS: preserved existing receiver role serial/gain/ppm values")
else:
    print("WARN: no existing receiver roles found to preserve")

validate_config_payload(merged)
print("PASS: TOPAZ/TRWC merged config validates")
print("SYSTEM_NAME=" + merged["systems"][0]["name"])
print("CONTROL_CHANNELS=" + ",".join(str(v) for v in merged["systems"][0]["control_channels_hz"]))
print("TALKGROUP_COUNT=" + str(len(merged["systems"][0]["talkgroups"])))

if mode == "dry-run":
    print("PASS: dry-run selected; runtime config was not changed")
    raise SystemExit(0)

runtime_path.parent.mkdir(parents=True, exist_ok=True)
if runtime_path.exists():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = runtime_path.with_name(runtime_path.name + f".bak.{stamp}")
    backup.write_text(runtime_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"PASS: backed up existing runtime config: {backup}")

runtime_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
print(f"PASS: wrote runtime config: {runtime_path}")
PY_INIT
then
  pass "TOPAZ/TRWC config initializer python step passed"
else
  fail "TOPAZ/TRWC config initializer python step failed"
fi

if [[ "$MODE" == "apply" && "$FAIL_COUNT" -eq 0 ]]; then
  if ./tools/p25_validate_config.sh >> "$REPORT_FILE" 2>&1; then
    pass "active config validator passed"
  else
    fail "active config validator failed"
  fi
  if ./tools/p25_generate_op25_config.sh >> "$REPORT_FILE" 2>&1; then
    pass "generated OP25 runtime config from TOPAZ/TRWC profile"
  else
    fail "OP25 config generation failed"
  fi
fi

finish
