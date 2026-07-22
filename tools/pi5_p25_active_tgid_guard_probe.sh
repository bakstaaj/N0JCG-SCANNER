#!/usr/bin/env bash
set -u

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

pass() { echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
finish() {
  echo "SUMMARY: PASS=${PASS_COUNT} WARN=${WARN_COUNT} FAIL=${FAIL_COUNT}"
  if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "FINAL: PASS"
    exit 0
  fi
  echo "FINAL: FAIL"
  exit 1
}
trap finish EXIT

echo "=== PI-P25-SCANNER active TGID guard probe ==="
if [ ! -d .git ] || [ ! -d src/pi_p25_scanner ]; then
  fail "run this script from the PI-P25-SCANNER repository root"
  exit 0
fi
pass "running from repository root"

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
  exit 0
fi

PYTHONPATH=src python3 - <<'PY_PROBE'
from pi_p25_scanner.runtime_activity import RuntimeActivityTracker
from pi_p25_scanner.runtime_status import RuntimeStatusParser

parser = RuntimeStatusParser()
tracker = RuntimeActivityTracker()

config_lines = [
    'added talkgroup 3105 from /home/pi/PI-P25-SCANNER/runtime/op25/TOPAZ_TRWC_Mesa_Simulcast_Test_whitelist.tsv',
    'loading talkgroup 3840 from /home/pi/PI-P25-SCANNER/runtime/op25/TOPAZ_TRWC_Mesa_Simulcast_Test_whiteli',
    'tgid 3899 from /home/pi/PI-P25-SCANNER/runtime/op25/TOPAZ_TRWC_Mesa_Simulcast_Test_blacklist.tsv',
]
config_summary = tracker.snapshot()
for config_line in config_lines:
    config_update = parser.parse_line(config_line)
    config_summary = tracker.record(config_update)
    if config_update.tgid is not None:
        raise SystemExit('configured whitelist/blacklist TGID was incorrectly treated as active')
    if 'configured_tgid_ignored_for_activity' not in config_update.parser_notes:
        raise SystemExit('configured TGID parser note missing')
if config_summary['talkgroup_updates'] != 0 or config_summary['unique_tgids']:
    raise SystemExit('configured whitelist/blacklist TGID changed active TGID counters')

active_line = 'voice grant tgid 3105 frequency 853.275000 label Mesa Fire Dispatch clear'
active_update = parser.parse_line(active_line)
active_summary = tracker.record(active_update)
if active_update.tgid != 3105:
    raise SystemExit('active voice TGID was not parsed')
if active_update.voice_frequency_hz != 853275000:
    raise SystemExit('active voice frequency was not parsed')
if active_summary['talkgroup_updates'] != 1 or active_summary['unique_tgids'] != [3105]:
    raise SystemExit('active TGID counters were not updated correctly')

frame_line = '07/05/26 13:46:12.803930 [0] IMBE (PLAINTEXT) 11 eb 7d errs 0'
frame_update = parser.parse_line(frame_line)
frame_summary = tracker.record(frame_update)
if frame_update.encrypted is not False or frame_update.muted is not False:
    raise SystemExit('plaintext voice frame was not classified as clear')
if frame_summary['clear_voice_events'] < 2:
    raise SystemExit('clear voice counter did not include active clear line and plaintext frame')

print('active_tgid_guard_summary', active_summary['talkgroup_updates'], active_summary['unique_tgids'], frame_summary['clear_voice_events'])
PY_PROBE
rc=$?
if [ "$rc" -eq 0 ]; then
  pass "active TGID guard fixture passed"
else
  fail "active TGID guard fixture failed rc=$rc"
fi
