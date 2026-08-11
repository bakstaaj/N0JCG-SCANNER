#!/usr/bin/env bash
set -u

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
TEMP_ROOT=""

pass() { echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
cleanup() {
  if [ -n "$TEMP_ROOT" ] && [ -d "$TEMP_ROOT" ]; then
    rm -rf "$TEMP_ROOT"
  fi
}
finish() {
  cleanup
  echo "SUMMARY: PASS=${PASS_COUNT} WARN=${WARN_COUNT} FAIL=${FAIL_COUNT}"
  if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "FINAL: PASS"
    exit 0
  fi
  echo "FINAL: FAIL"
  exit 1
}
trap finish EXIT

echo "=== scanner OP25 discovery trust probe ==="
if [ ! -d .git ] || [ ! -d src/pi_p25_scanner ]; then
  fail "run this script from the scanner repository root"
  exit 0
fi
pass "running from repository root"

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
  exit 0
fi

TEMP_ROOT="$(mktemp -d)"
KNOWN_APP="$TEMP_ROOT/op25/op25/gr-op25_repeater/apps/rx.py"
GENERIC_APP="$TEMP_ROOT/not-op25/rx.py"
mkdir -p "$(dirname "$KNOWN_APP")" "$(dirname "$GENERIC_APP")"
printf '%s\n' '# fixture' > "$KNOWN_APP"
printf '%s\n' '# fixture' > "$GENERIC_APP"

if P25_SCANNER_OP25_COMMAND="$KNOWN_APP" PYTHONPATH=src python3 - <<'PYTRUST'
from pi_p25_scanner.decoder_discovery import discover_op25
capability = discover_op25()
if not capability.installed:
    raise SystemExit('fixture OP25 path was not discovered')
if not capability.trusted:
    raise SystemExit('known OP25 source-tree rx.py was not trusted')
if capability.trusted_reason != 'known_op25_source_tree_app':
    raise SystemExit(f'unexpected trust reason: {capability.trusted_reason}')
if any('generic rx.py' in warning for warning in capability.warnings):
    raise SystemExit('trusted OP25 source-tree rx.py still emitted generic warning')
print('trusted_fixture', capability.command, capability.trusted_reason)
PYTRUST
then
  pass "known OP25 source-tree rx.py is trusted without generic warning"
else
  fail "known OP25 source-tree trust fixture failed"
fi

if P25_SCANNER_OP25_COMMAND="$GENERIC_APP" PYTHONPATH=src python3 - <<'PYGENERIC'
from pi_p25_scanner.decoder_discovery import discover_op25
capability = discover_op25()
if not capability.installed:
    raise SystemExit('generic fixture rx.py was not discovered')
if capability.trusted:
    raise SystemExit('generic rx.py was incorrectly trusted')
if not any('generic rx.py' in warning for warning in capability.warnings):
    raise SystemExit('generic rx.py warning was not emitted for untrusted rx.py')
print('generic_fixture', capability.command, capability.warnings[0])
PYGENERIC
then
  pass "untrusted generic rx.py still emits warning"
else
  fail "generic rx.py warning fixture failed"
fi

if PYTHONPATH=src python3 -m pi_p25_scanner.decoder_discovery --json >/tmp/pi_p25_decoder_discovery_probe.json; then
  pass "decoder discovery JSON command works"
else
  fail "decoder discovery JSON command failed"
fi
