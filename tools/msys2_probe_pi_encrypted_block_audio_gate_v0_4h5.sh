#!/usr/bin/env bash
set -Eeuo pipefail
PI_HOST="192.168.254.63"
COUNT="${1:-20}"
python3 - "$PI_HOST" "$COUNT" <<'PY'
import json, sys, time, urllib.request
host = sys.argv[1]
count = int(sys.argv[2])
for i in range(count):
    for path in ("/api/activity", "/api/status"):
        try:
            with urllib.request.urlopen(f"http://{host}:8070{path}", timeout=2.0) as r:
                d = json.loads(r.read().decode("utf-8"))
            bt = d.get("blocked_talkgroups") or {}
            print(f"{i:02d} {path} state={d.get('scanner_state')} active={d.get('active_tgid')} label={d.get('active_talkgroup_label')!r} suppressed={d.get('suppressed_active_tgid')} encrypted={d.get('encrypted')} muted={d.get('muted')} blocked_count={bt.get('count')}")
        except Exception as exc:
            print(f"{i:02d} {path} ERROR {exc!r}")
    try:
        with urllib.request.urlopen(f"http://{host}:8072/api/audio/status", timeout=2.0) as r:
            a = json.loads(r.read().decode("utf-8"))
        print(f"{i:02d} /api/audio/status log_gate_active={a.get('log_gate_active')} remaining={a.get('log_gate_remaining_seconds')} events={a.get('log_gate_events')} last_reason={a.get('last_log_gate_reason')!r} dropped={a.get('audio_dropped_by_log_gate')}")
    except Exception as exc:
        print(f"{i:02d} /api/audio/status ERROR {exc!r}")
    time.sleep(0.75)
PY
