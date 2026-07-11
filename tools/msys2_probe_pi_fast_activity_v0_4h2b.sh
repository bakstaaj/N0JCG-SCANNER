#!/usr/bin/env bash
set -Eeuo pipefail
[[ -f .env ]] && set -a && source ./.env && set +a || true
PI_HOST="${PI_HOST:-192.168.254.63}"
for i in {1..30}; do
  printf '%02d ' "$i"
  python3 - "$PI_HOST" <<'PY'
import json, sys, urllib.request
host = sys.argv[1]
try:
    with urllib.request.urlopen(f"http://{host}:8070/api/activity", timeout=2) as r:
        p = json.loads(r.read().decode())
    print(json.dumps({
        "state": p.get("scanner_state"),
        "active_tgid": p.get("active_tgid"),
        "label": p.get("active_talkgroup_label"),
        "last_tgid": p.get("last_active_tgid"),
        "last_label": p.get("last_active_talkgroup_label"),
        "voice_hz": p.get("active_voice_frequency_hz"),
        "updated": p.get("updated_utc"),
    }, sort_keys=True))
except Exception as exc:
    print("ERR", repr(exc))
PY
  sleep 0.55
done
