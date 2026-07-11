#!/usr/bin/env bash
set -Eeuo pipefail
PI_HOST="192.168.254.63"
python3 - <<PY
import json, time, urllib.request
base = "http://${PI_HOST}:8070"
for i in range(60):
    try:
        with urllib.request.urlopen(base + "/api/activity", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        print(json.dumps({
            "i": i,
            "scanner_state": data.get("scanner_state"),
            "active_tgid": data.get("active_tgid"),
            "label": data.get("active_talkgroup_label"),
            "voice_hz": data.get("active_voice_frequency_hz"),
            "encrypted": data.get("encrypted"),
            "muted": data.get("muted"),
            "last_event": data.get("last_event"),
        }, sort_keys=True))
    except Exception as exc:
        print("PROBE_ERROR", repr(exc))
    time.sleep(0.5)
PY
