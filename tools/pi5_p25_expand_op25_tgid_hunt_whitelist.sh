#!/usr/bin/env bash
# Expand the active OP25 whitelist for TOPAZ/TRWC clear-traffic hunting.
# Writes only runtime/op25 files; checked-in config is not modified.
set -Eeuo pipefail

START_TGID=2500
END_TGID=4500
MODE="dry-run"
YES=0
BLACKLIST_KNOWN_ENCRYPTED=1
USE_LOG_DISCOVERY=1
REPORT_DIR=".p25_tgid_hunt_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/tgid_hunt_expand_${STAMP}.txt"
MANIFEST="runtime/op25/manifest.json"

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
finish() {
  printf '\nSUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
    exit 0
  fi
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
}
trap 'fail "script stopped unexpectedly at line $LINENO"; finish' ERR

usage() {
  cat <<'USAGE'
Usage:
  ./tools/pi5_p25_expand_op25_tgid_hunt_whitelist.sh --dry-run [options]
  ./tools/pi5_p25_expand_op25_tgid_hunt_whitelist.sh --apply --yes [options]

Expands the generated OP25 whitelist for short clear-traffic hunting tests.
This changes only ignored runtime/op25 files, not checked-in config.

Options:
  --start N                     First TGID to include. Default: 2500
  --end N                       Last TGID to include. Default: 4500
  --blacklist-known-encrypted   Exclude/blacklist known encrypted TGIDs. Default
  --include-known-encrypted     Do not blacklist known encrypted TGIDs
  --from-log                    Add encrypted TGIDs discovered in recent OP25 logs to blacklist. Default
  --no-log                      Do not inspect recent OP25 logs
  --dry-run                     Show planned counts without writing files. Default
  --apply                       Write runtime/op25 whitelist/tag/blacklist files
  --yes                         Required with --apply
  -h, --help                    Show help

Recommended first pass:
  ./tools/pi5_p25_expand_op25_tgid_hunt_whitelist.sh --apply --yes --start 2500 --end 4500
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start) shift; START_TGID="$1"; shift ;;
    --end) shift; END_TGID="$1"; shift ;;
    --blacklist-known-encrypted) BLACKLIST_KNOWN_ENCRYPTED=1; shift ;;
    --include-known-encrypted) BLACKLIST_KNOWN_ENCRYPTED=0; shift ;;
    --from-log) USE_LOG_DISCOVERY=1; shift ;;
    --no-log) USE_LOG_DISCOVERY=0; shift ;;
    --dry-run) MODE="dry-run"; shift ;;
    --apply) MODE="apply"; shift ;;
    --yes) YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'FAIL: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"
printf '=== scanner V0.3L OP25 TGID hunt whitelist expansion ===\n' | tee -a "$REPORT_FILE"
printf 'Started UTC: %s\n' "$STAMP" | tee -a "$REPORT_FILE"
printf 'Working directory: %s\n' "$(pwd)" | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "runtime" && -d "tools" ]]; then
  pass "running from repository root"
else
  fail "run from scanner repository root"
  finish
fi
if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
fi
if ! [[ "$START_TGID" =~ ^[0-9]+$ && "$END_TGID" =~ ^[0-9]+$ ]]; then
  fail "start/end TGIDs must be integers"
fi
if [[ "$START_TGID" -lt 1 || "$END_TGID" -lt "$START_TGID" || "$END_TGID" -gt 65535 ]]; then
  fail "invalid TGID range: start=$START_TGID end=$END_TGID"
fi
if [[ "$MODE" == "apply" && "$YES" -ne 1 ]]; then
  fail "--apply requires --yes"
fi
if [[ "$FAIL_COUNT" -ne 0 ]]; then
  finish
fi

if [[ ! -f "$MANIFEST" ]]; then
  warn "OP25 manifest missing; attempting to generate runtime OP25 config"
  if ./tools/p25_generate_op25_config.sh >> "$REPORT_FILE" 2>&1; then
    pass "generated OP25 runtime config"
  else
    fail "could not generate OP25 runtime config"
    finish
  fi
fi
if [[ -f "$MANIFEST" ]]; then
  pass "OP25 manifest exists: $MANIFEST"
else
  fail "OP25 manifest still missing: $MANIFEST"
  finish
fi

PYTHONPATH=src python3 - "$MANIFEST" "$START_TGID" "$END_TGID" "$MODE" "$BLACKLIST_KNOWN_ENCRYPTED" "$USE_LOG_DISCOVERY" "$STAMP" <<'PY_EXPAND' | tee -a "$REPORT_FILE"
from __future__ import annotations
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

manifest_path = Path(sys.argv[1])
start_tgid = int(sys.argv[2])
end_tgid = int(sys.argv[3])
mode = sys.argv[4]
blacklist_known = sys.argv[5] == "1"
use_log = sys.argv[6] == "1"
stamp = sys.argv[7]

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
systems = manifest.get("systems") or []
if not systems:
    raise SystemExit("FAIL: runtime OP25 manifest has no systems")
system = systems[0]
whitelist_file = Path(system["whitelist_file"])
tags_file = Path(system["tags_file"])
blacklist_file = Path(system["blacklist_file"])

for path, label in [(whitelist_file, "whitelist"), (tags_file, "tags"), (blacklist_file, "blacklist")]:
    if not path.exists():
        if label == "blacklist":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        else:
            raise SystemExit(f"FAIL: expected {label} file missing: {path}")

existing_tags: dict[int, str] = {}
if tags_file.exists():
    for raw in tags_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t", 1)
        try:
            tgid = int(parts[0])
        except ValueError:
            continue
        label = parts[1].strip() if len(parts) > 1 else f"TGID {tgid}"
        existing_tags[tgid] = label

existing_whitelist = set()
if whitelist_file.exists():
    for raw in whitelist_file.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw.isdigit():
            existing_whitelist.add(int(raw))

# Seed from known encrypted talkgroups already seen during testing.
known_encrypted: dict[int, str] = {
    2900: "MPD A1 Fiesta District encrypted",
    2901: "MPD A2 Central District encrypted",
    2902: "MPD A3 Red Mountain encrypted",
    2903: "MPD A4 Superstition encrypted",
    2904: "MPD A5 Gateway encrypted",
    3107: "AJPD 1 Dispatch encrypted",
    3840: "QC PD Dispatch encrypted",
}

log_labels: dict[int, str] = {}
if use_log:
    log_dir = Path(".p25_browser_audio_live_reports")
    logs = sorted(log_dir.glob("op25_audio_*.log"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)[:12]
    for log in logs:
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = re.search(r"new\s+tgid=(\d+)\s+(.+)", line, re.IGNORECASE)
            if m:
                tgid = int(m.group(1))
                label = m.group(2).strip()
                log_labels.setdefault(tgid, label)
                if "encrypt" in label.lower():
                    known_encrypted.setdefault(tgid, label)
            for pattern in (r"skip encrypted call:\s*tg\((\d+)\)", r"encrypted[^\n]*tg\(?=?(\d+)\)?"):
                m2 = re.search(pattern, line, re.IGNORECASE)
                if m2:
                    known_encrypted.setdefault(int(m2.group(1)), f"encrypted observed in OP25 log")

hunt_range = set(range(start_tgid, end_tgid + 1))
blacklist = set(known_encrypted) if blacklist_known else set()
whitelist = sorted((hunt_range | existing_whitelist) - blacklist)
all_tag_ids = sorted((hunt_range | existing_whitelist | set(existing_tags) | set(known_encrypted)) & set(range(1, 65536)))

print(f"SYSTEM_NAME={system.get('name', 'unknown')}")
print(f"TGID_HUNT_RANGE={start_tgid}-{end_tgid}")
print(f"EXISTING_WHITELIST_COUNT={len(existing_whitelist)}")
print(f"HUNT_RANGE_COUNT={len(hunt_range)}")
print(f"KNOWN_ENCRYPTED_BLACKLIST_COUNT={len(blacklist)}")
print(f"EXPANDED_WHITELIST_COUNT={len(whitelist)}")
print(f"WHITELIST_FILE={whitelist_file}")
print(f"TAGS_FILE={tags_file}")
print(f"BLACKLIST_FILE={blacklist_file}")
if blacklist:
    print("BLACKLISTED_TGIDS=" + ",".join(str(v) for v in sorted(blacklist)))

if mode == "dry-run":
    print("PASS: dry-run selected; runtime OP25 files were not changed")
    raise SystemExit(0)

for path in (whitelist_file, tags_file, blacklist_file):
    if path.exists():
        backup = path.with_name(path.name + f".bak.{stamp}")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"PASS: backed up {path} to {backup}")

whitelist_file.write_text("\n".join(str(tgid) for tgid in whitelist) + "\n", encoding="utf-8")
blacklist_file.write_text("\n".join(str(tgid) for tgid in sorted(blacklist)) + ("\n" if blacklist else ""), encoding="utf-8")

tag_lines = []
for tgid in all_tag_ids:
    if tgid in existing_tags:
        label = existing_tags[tgid]
    elif tgid in known_encrypted:
        label = known_encrypted[tgid]
    elif tgid in log_labels:
        label = log_labels[tgid]
    else:
        label = f"Discovery TGID {tgid}"
    tag_lines.append(f"{tgid}\t{label}")
tags_file.write_text("\n".join(tag_lines) + "\n", encoding="utf-8")

print(f"PASS: wrote expanded whitelist: {whitelist_file}")
print(f"PASS: wrote tag file: {tags_file}")
print(f"PASS: wrote blacklist file: {blacklist_file}")
print(f"EXPANDED_WHITELIST_FINAL_COUNT={len(whitelist)}")
print(f"BLACKLIST_FINAL_COUNT={len(blacklist)}")
PY_EXPAND

pass "TGID hunt whitelist expansion python step passed"
finish
