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

echo "=== PI-P25-SCANNER OP25 voice frame parser probe ==="

if [ ! -d .git ] || [ ! -d src/pi_p25_scanner ]; then
  fail "run from PI-P25-SCANNER repository root"
  exit 0
fi
pass "running from repository root"

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
  exit 0
fi

if PYTHONPATH=src python3 - <<'PYVOICE'
from pi_p25_scanner.runtime_activity import RuntimeActivityTracker
from pi_p25_scanner.runtime_status import RuntimeStatusParser

parser = RuntimeStatusParser()
tracker = RuntimeActivityTracker()

samples = [
    "07/05/26 13:28:29.529056 [0] IMBE (PLAINTEXT) 20 b9 cb 29 fb b6 4e 6c ff 06 1a errs 0",
    "07/05/26 13:28:29.800412 [0] AMBE (PLAINTEXT) 5a f9 2b b4 errs 0",
    "07/05/26 13:28:30.000000 [0] IMBE (ENCRYPTED) aa bb cc errs 0",
    "voice grant tgid 3105 frequency 853.275000 label Mesa Fire Dispatch clear",
]
for line in samples:
    tracker.record(parser.parse_line(line))

summary = tracker.snapshot()
assert summary["parsed_status_lines"] == 4, summary
assert summary["clear_voice_events"] >= 3, summary
assert summary["encrypted_events"] >= 1, summary
assert summary["muted_events"] >= 1, summary
assert summary["talkgroup_updates"] >= 1, summary
assert 3105 in summary["unique_tgids"], summary
print("voice_frame_parser_summary", summary["clear_voice_events"], summary["encrypted_events"], summary["unique_tgids"])
PYVOICE
then
  pass "runtime parser/tracker voice-frame fixture passed"
else
  fail "runtime parser/tracker voice-frame fixture failed"
fi

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pi-p25-v0-2k-evidence.XXXXXX")"
JSONL="$TMP_ROOT/live_activity_fixture.jsonl"
cat > "$JSONL" <<'JSONL'
{"scanner_state":"running","decoder_process":{"running":true},"active_control_frequency_hz":852750000,"activity_summary":{"parsed_status_lines":2,"control_frequency_updates":1,"clear_voice_events":0,"encrypted_events":0,"muted_events":0,"unique_tgids":[]}}
{"scanner_state":"running","decoder_process":{"running":true},"active_control_frequency_hz":852750000,"activity_summary":{"parsed_status_lines":12,"control_frequency_updates":1,"clear_voice_events":8,"encrypted_events":1,"muted_events":1,"unique_tgids":[]}}
JSONL

if ./tools/pi5_p25_live_evidence_analyze.sh --path "$JSONL" > "$TMP_ROOT/analyzer.txt" 2>&1; then
  if grep -q "clear voice evidence observed" "$TMP_ROOT/analyzer.txt"; then
    pass "evidence analyzer accepts clear voice activity counters"
  else
    fail "evidence analyzer did not report clear voice activity counters"
    cat "$TMP_ROOT/analyzer.txt"
  fi
else
  fail "evidence analyzer JSONL fixture failed"
  cat "$TMP_ROOT/analyzer.txt"
fi

rm -rf "$TMP_ROOT"
