#!/usr/bin/env bash
set -u

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_runtime_activity_probe_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${REPORT_DIR}/runtime_activity_${STAMP}.txt"
mkdir -p "${REPORT_DIR}"
: > "${REPORT}"

log() { printf '%s\n' "$*" | tee -a "${REPORT}"; }
pass() { PASS_COUNT=$((PASS_COUNT + 1)); log "PASS: $*"; }
warn() { WARN_COUNT=$((WARN_COUNT + 1)); log "WARN: $*"; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); log "FAIL: $*"; }
finish() {
  log "Report: ${REPORT}"
  log "SUMMARY: PASS=${PASS_COUNT} WARN=${WARN_COUNT} FAIL=${FAIL_COUNT}"
  if [ "${FAIL_COUNT}" -eq 0 ]; then
    log "FINAL: PASS"
    exit 0
  fi
  log "FINAL: FAIL"
  exit 1
}
trap finish EXIT

log "=== scanner runtime activity probe ==="

if [ ! -d .git ] || [ ! -d src/pi_p25_scanner ]; then
  fail "run from repository root"
  exit 1
fi
pass "running from repository root"

if ! PYTHONPATH=src python3 - <<'PY'
from pi_p25_scanner.runtime_activity import RuntimeActivityTracker
from pi_p25_scanner.runtime_status import RuntimeStatusParser

parser = RuntimeStatusParser()
tracker = RuntimeActivityTracker()

lines = [
    "control channel frequency 852.750000",
    "voice grant tgid 3025 frequency 853.275000 label Mesa Fire Dispatch clear Phase II",
    "voice grant tgid 3105 frequency 852.825000 label EMS encrypted muted Phase II",
    "voice grant talkgroup 3025 frequency 853.350000 label Gilbert Fire Dispatch clear Phase II",
]

for line in lines:
    tracker.record(parser.parse_line(line))

snapshot = tracker.snapshot()
assert snapshot["parsed_status_lines"] == 4, snapshot
assert snapshot["control_frequency_updates"] == 1, snapshot
assert snapshot["voice_frequency_updates"] == 3, snapshot
assert snapshot["talkgroup_updates"] == 3, snapshot
assert snapshot["unique_tgid_count"] == 2, snapshot
assert snapshot["unique_tgids"] == [3025, 3105], snapshot
assert snapshot["encrypted_events"] == 1, snapshot
assert snapshot["muted_events"] == 1, snapshot
assert snapshot["clear_voice_events"] == 2, snapshot
assert len(snapshot["recent_events"]) == 4, snapshot

tracker.reset()
reset = tracker.snapshot()
assert reset["parsed_status_lines"] == 0, reset
assert reset["unique_tgid_count"] == 0, reset
PY
then
  fail "runtime activity tracker assertions failed"
  exit 1
fi
pass "runtime activity tracker assertions passed"

if ! PYTHONPATH=src python3 - <<'PY'
import json
from pi_p25_scanner.backend import ScannerManager

manager = ScannerManager()
manager._append_log("control channel frequency 852.750000")
manager._append_log("voice grant tgid 3025 frequency 853.275000 label Mesa Fire Dispatch clear Phase II")
manager._append_log("voice grant tgid 3105 frequency 852.825000 label EMS encrypted muted Phase II")
status = manager.status_payload()
activity = status.get("activity_summary", {})
assert activity.get("parsed_status_lines") == 3, json.dumps(activity, indent=2)
assert activity.get("voice_frequency_updates") == 2, json.dumps(activity, indent=2)
assert activity.get("unique_tgid_count") == 2, json.dumps(activity, indent=2)
assert activity.get("encrypted_events") == 1, json.dumps(activity, indent=2)
assert status.get("active_tgid") == 3105, json.dumps(status, indent=2)
PY
then
  fail "backend activity summary assertions failed"
  exit 1
fi
pass "backend activity summary assertions passed"

for marker in activityParsedLines activityRecentEvents activity_summary renderActivitySummary RuntimeActivityTracker; do
  if ! python3 - "$marker" <<'PY'
from pathlib import Path
import sys
marker = sys.argv[1]
paths = [Path("web/index.html"), Path("web/app.js"), Path("src/pi_p25_scanner/backend.py"), Path("src/pi_p25_scanner/runtime_activity.py")]
if not any(marker in path.read_text(encoding="utf-8") for path in paths if path.exists()):
    raise SystemExit(1)
PY
  then
    fail "missing expected activity marker: ${marker}"
  else
    pass "activity marker present: ${marker}"
  fi
done
