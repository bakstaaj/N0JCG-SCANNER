#!/usr/bin/env bash
set -u

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
BACKEND_URL="${P25_SCANNER_BACKEND_URL:-http://127.0.0.1:8070}"
SECONDS_TO_RUN=180
INTERVAL_SECONDS=3
YES=false
NO_START=false
SELF_TEST=false
STARTED_BY_PROBE=false

pass() { echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }

cleanup_started_process() {
  if [ "$STARTED_BY_PROBE" = true ]; then
    if command -v curl >/dev/null 2>&1; then
      if curl -fsS -X POST "$BACKEND_URL/api/scanner/stop" >/dev/null 2>&1; then
        STARTED_BY_PROBE=false
        warn "scanner stopped by cleanup after capture interruption"
      else
        warn "cleanup could not stop scanner started by capture"
      fi
    fi
  fi
}

finish() {
  cleanup_started_process
  echo "SUMMARY: PASS=${PASS_COUNT} WARN=${WARN_COUNT} FAIL=${FAIL_COUNT}"
  if [ "${FAIL_COUNT}" -eq 0 ]; then
    echo "FINAL: PASS"
    exit 0
  fi
  echo "FINAL: FAIL"
  exit 1
}

trap finish EXIT

usage() {
  cat <<'USAGE'
Usage: tools/pi5_p25_live_activity_capture.sh [options]

Capture backend /api/status snapshots while the validated OP25 scanner runs.

Options:
  --seconds N       Capture duration in seconds. Default: 180.
  --interval N      Poll interval in seconds. Default: 3.
  --backend-url URL Backend base URL. Default: http://127.0.0.1:8070.
  --no-start        Do not POST /api/scanner/start; only observe current state.
  --yes             Required for a live capture unless --self-test is used.
  --self-test       Validate the summarizer using fixture snapshots only.
  -h, --help        Show this help.

Reports are written under .p25_live_activity_capture_reports/ by default. Copies
of summary JSON and snapshot JSONL are written under runtime/evidence/ when
runtime is available. Set P25_SCANNER_CAPTURE_REPORT_DIR to override the report directory.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --seconds)
      shift
      SECONDS_TO_RUN="${1:-}"
      ;;
    --interval)
      shift
      INTERVAL_SECONDS="${1:-}"
      ;;
    --backend-url)
      shift
      BACKEND_URL="${1:-}"
      ;;
    --no-start)
      NO_START=true
      ;;
    --yes)
      YES=true
      ;;
    --self-test)
      SELF_TEST=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      usage
      exit 0
      ;;
  esac
  shift
done

is_positive_int() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
    *) [ "$1" -gt 0 ] ;;
  esac
}

require_command() {
  if command -v "$1" >/dev/null 2>&1; then
    pass "command available: $1"
  else
    fail "missing required command: $1"
  fi
}

json_summary() {
  local jsonl_path="$1"
  local summary_json="$2"
  local summary_txt="$3"
  python3 - "$jsonl_path" "$summary_json" "$summary_txt" <<'PYSUMMARY'
import json
import sys
from pathlib import Path
from typing import Any

jsonl_path = Path(sys.argv[1])
summary_json = Path(sys.argv[2])
summary_txt = Path(sys.argv[3])

snapshots: list[dict[str, Any]] = []
for raw in jsonl_path.read_text(encoding="utf-8").splitlines():
    raw = raw.strip()
    if not raw:
        continue
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        continue
    if isinstance(payload, dict):
        snapshots.append(payload)


def walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def number_at(payload: dict[str, Any], names: tuple[str, ...]) -> int:
    best = 0
    for item in walk_dicts(payload):
        for name in names:
            value = item.get(name)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                best = max(best, value)
            elif isinstance(value, float):
                best = max(best, int(value))
    return best


def list_at(payload: dict[str, Any], names: tuple[str, ...]) -> list[Any]:
    values: list[Any] = []
    for item in walk_dicts(payload):
        for name in names:
            value = item.get(name)
            if isinstance(value, list):
                values.extend(value)
            elif isinstance(value, dict):
                values.extend(value.keys())
    return values

states: list[str] = []
running_count = 0
active_tgids: set[int] = set()
recent_events: list[Any] = []
for payload in snapshots:
    state = payload.get("scanner_state")
    if isinstance(state, str) and state not in states:
        states.append(state)
    process = payload.get("decoder_process")
    if isinstance(process, dict) and process.get("running") is True:
        running_count += 1
    tgid = payload.get("active_tgid")
    if isinstance(tgid, int):
        active_tgids.add(tgid)
    for value in list_at(payload, ("unique_tgids", "observed_tgids", "tgids")):
        try:
            active_tgids.add(int(value if not isinstance(value, dict) else value.get("tgid")))
        except (TypeError, ValueError):
            pass
    recent_events.extend(list_at(payload, ("recent_activity", "recent_events", "activity_events")))

summary = {
    "ok": bool(snapshots),
    "snapshot_count": len(snapshots),
    "running_snapshot_count": running_count,
    "states_seen": states,
    "max_parsed_status_lines": max((number_at(p, ("parsed_status_lines", "parsed_lines", "status_line_count")) for p in snapshots), default=0),
    "max_control_frequency_updates": max((number_at(p, ("control_frequency_updates", "control_updates")) for p in snapshots), default=0),
    "max_voice_frequency_updates": max((number_at(p, ("voice_frequency_updates", "voice_updates")) for p in snapshots), default=0),
    "max_talkgroup_updates": max((number_at(p, ("talkgroup_updates", "tgid_updates")) for p in snapshots), default=0),
    "max_clear_voice_events": max((number_at(p, ("clear_voice_events", "clear_events")) for p in snapshots), default=0),
    "max_encrypted_events": max((number_at(p, ("encrypted_events", "encryption_events")) for p in snapshots), default=0),
    "max_muted_events": max((number_at(p, ("muted_events", "skipped_events", "muted_skipped_events")) for p in snapshots), default=0),
    "unique_tgids": sorted(active_tgids),
    "recent_event_sample_count": len(recent_events),
}
summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
lines = [
    "PI-P25-SCANNER live activity capture summary",
    f"Snapshots: {summary['snapshot_count']}",
    f"Running snapshots: {summary['running_snapshot_count']}",
    f"States seen: {', '.join(states) if states else '-'}",
    f"Parsed status lines: {summary['max_parsed_status_lines']}",
    f"Control updates: {summary['max_control_frequency_updates']}",
    f"Voice updates: {summary['max_voice_frequency_updates']}",
    f"Talkgroup updates: {summary['max_talkgroup_updates']}",
    f"Clear voice events: {summary['max_clear_voice_events']}",
    f"Encrypted events: {summary['max_encrypted_events']}",
    f"Muted/skipped events: {summary['max_muted_events']}",
    f"Unique TGIDs: {', '.join(str(t) for t in summary['unique_tgids']) if summary['unique_tgids'] else '-'}",
]
summary_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps(summary, sort_keys=True))
PYSUMMARY
}

append_snapshot() {
  local source_json="$1"
  local jsonl_path="$2"
  python3 - "$source_json" "$jsonl_path" <<'PYAPPEND'
import json
import sys
from pathlib import Path
source = Path(sys.argv[1])
jsonl = Path(sys.argv[2])
payload = json.loads(source.read_text(encoding="utf-8"))
with jsonl.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
PYAPPEND
}

check_summary_field() {
  local summary_json="$1"
  local field_name="$2"
  python3 - "$summary_json" "$field_name" <<'PYCHECK'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
value = payload.get(sys.argv[2])
if isinstance(value, bool):
    raise SystemExit(0 if value else 1)
if isinstance(value, int):
    raise SystemExit(0 if value > 0 else 1)
if isinstance(value, list):
    raise SystemExit(0 if value else 1)
raise SystemExit(1)
PYCHECK
}

if [ ! -d .git ] || [ ! -d tools ] || [ ! -d src/pi_p25_scanner ]; then
  fail "run this script from the PI-P25-SCANNER repository root"
  exit 0
fi
pass "running from repository root"

require_command python3
require_command date
require_command mkdir

if ! is_positive_int "$SECONDS_TO_RUN"; then
  fail "--seconds must be a positive integer"
fi
if ! is_positive_int "$INTERVAL_SECONDS"; then
  fail "--interval must be a positive integer"
fi
if [ "${FAIL_COUNT}" -gt 0 ]; then
  exit 0
fi
pass "capture timing arguments valid"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_DIR="${P25_SCANNER_CAPTURE_REPORT_DIR:-.p25_live_activity_capture_reports}"
mkdir -p "$REPORT_DIR"
JSONL="$REPORT_DIR/live_activity_${STAMP}.jsonl"
SUMMARY_JSON="$REPORT_DIR/live_activity_summary_${STAMP}.json"
SUMMARY_TXT="$REPORT_DIR/live_activity_summary_${STAMP}.txt"

if [ "$SELF_TEST" = true ]; then
  cat > "$JSONL" <<'JSONL'
{"scanner_state":"running","decoder_process":{"running":true},"active_tgid":3105,"activity_summary":{"parsed_status_lines":2,"control_frequency_updates":1,"voice_frequency_updates":1,"talkgroup_updates":1,"clear_voice_events":1,"encrypted_events":0,"muted_events":0,"unique_tgids":[3105],"recent_activity":[{"tgid":3105}]}}
{"scanner_state":"running","decoder_process":{"running":true},"active_tgid":3105,"activity_summary":{"parsed_status_lines":3,"control_frequency_updates":1,"voice_frequency_updates":2,"talkgroup_updates":2,"clear_voice_events":1,"encrypted_events":1,"muted_events":1,"unique_tgids":[3105,1201],"recent_activity":[{"tgid":1201,"encrypted":true}]}}
JSONL
  if json_summary "$JSONL" "$SUMMARY_JSON" "$SUMMARY_TXT" >/dev/null; then
    pass "self-test summary generated"
  else
    fail "self-test summary generation failed"
    exit 0
  fi
  if check_summary_field "$SUMMARY_JSON" snapshot_count; then
    pass "self-test snapshot count valid"
  else
    fail "self-test snapshot count missing"
  fi
  if check_summary_field "$SUMMARY_JSON" unique_tgids; then
    pass "self-test unique TGIDs detected"
  else
    fail "self-test unique TGIDs missing"
  fi
  echo "Report: $SUMMARY_TXT"
  echo "Summary JSON: $SUMMARY_JSON"
  exit 0
fi

require_command curl
if [ "${FAIL_COUNT}" -gt 0 ]; then
  exit 0
fi

if [ "$YES" != true ]; then
  fail "live capture requires --yes"
  exit 0
fi
pass "live capture confirmation present"

INITIAL_JSON="$REPORT_DIR/status_initial_${STAMP}.json"
if curl -fsS "$BACKEND_URL/api/status" > "$INITIAL_JSON"; then
  pass "backend reachable: $BACKEND_URL"
else
  fail "backend not reachable at $BACKEND_URL"
  exit 0
fi

append_snapshot "$INITIAL_JSON" "$JSONL"
RUNNING_NOW=false
if python3 - "$INITIAL_JSON" <<'PYRUNNING'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
process = payload.get("decoder_process") or {}
raise SystemExit(0 if process.get("running") is True else 1)
PYRUNNING
then
  RUNNING_NOW=true
fi

if [ "$RUNNING_NOW" = true ]; then
  pass "scanner was already running; probe will not stop it"
elif [ "$NO_START" = true ]; then
  warn "scanner is not running and --no-start was requested; capture will observe backend idle state"
else
  START_JSON="$REPORT_DIR/status_start_${STAMP}.json"
  if curl -fsS -X POST "$BACKEND_URL/api/scanner/start" > "$START_JSON"; then
    STARTED_BY_PROBE=true
    pass "scanner start requested through backend API"
    append_snapshot "$START_JSON" "$JSONL"
  else
    fail "scanner start request failed"
    exit 0
  fi
fi

END_TIME=$(( $(date +%s) + SECONDS_TO_RUN ))
SAMPLE_INDEX=0
while [ "$(date +%s)" -lt "$END_TIME" ]; do
  SAMPLE_INDEX=$((SAMPLE_INDEX + 1))
  SAMPLE_JSON="$REPORT_DIR/status_${STAMP}_${SAMPLE_INDEX}.json"
  if curl -fsS "$BACKEND_URL/api/status" > "$SAMPLE_JSON"; then
    append_snapshot "$SAMPLE_JSON" "$JSONL"
  else
    warn "status sample failed: $SAMPLE_INDEX"
  fi
  sleep "$INTERVAL_SECONDS"
done

FINAL_JSON="$REPORT_DIR/status_final_${STAMP}.json"
if curl -fsS "$BACKEND_URL/api/status" > "$FINAL_JSON"; then
  append_snapshot "$FINAL_JSON" "$JSONL"
  pass "final status snapshot captured"
else
  warn "final status snapshot failed"
fi

if [ "$STARTED_BY_PROBE" = true ]; then
  STOP_JSON="$REPORT_DIR/status_stop_${STAMP}.json"
  if curl -fsS -X POST "$BACKEND_URL/api/scanner/stop" > "$STOP_JSON"; then
    STARTED_BY_PROBE=false
    append_snapshot "$STOP_JSON" "$JSONL"
    pass "scanner stopped after capture"
  else
    warn "scanner stop request failed after capture"
  fi
fi

SUMMARY_PAYLOAD="$(json_summary "$JSONL" "$SUMMARY_JSON" "$SUMMARY_TXT")"
if [ -s "$SUMMARY_JSON" ] && [ -s "$SUMMARY_TXT" ]; then
  pass "capture summary generated"
else
  fail "capture summary missing"
fi

if check_summary_field "$SUMMARY_JSON" snapshot_count; then
  pass "captured one or more status snapshots"
else
  fail "no status snapshots captured"
fi

if check_summary_field "$SUMMARY_JSON" running_snapshot_count; then
  pass "capture included scanner running state"
else
  warn "capture did not observe scanner running state"
fi

if check_summary_field "$SUMMARY_JSON" unique_tgids; then
  pass "capture observed one or more TGIDs"
else
  warn "capture did not observe TGID activity during this window"
fi

mkdir -p runtime/evidence
EVIDENCE_JSON="runtime/evidence/live_activity_summary_${STAMP}.json"
EVIDENCE_JSONL="runtime/evidence/live_activity_${STAMP}.jsonl"
cp "$SUMMARY_JSON" "$EVIDENCE_JSON"
cp "$JSONL" "$EVIDENCE_JSONL"
pass "copied summary evidence: $EVIDENCE_JSON"
pass "copied snapshot evidence: $EVIDENCE_JSONL"

printf '%s\n' "$SUMMARY_PAYLOAD" > "$REPORT_DIR/live_activity_summary_${STAMP}.compact.json"
echo "Report: $SUMMARY_TXT"
echo "Summary JSON: $SUMMARY_JSON"
echo "Snapshot JSONL: $JSONL"
