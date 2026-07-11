#!/usr/bin/env bash
set -Eeuo pipefail
if [[ -f .env ]]; then set -a; source .env; set +a; fi
PI_HOST="${PI_HOST:-192.168.254.63}"
for i in $(seq 1 30); do
  python3 - "$PI_HOST" <<'PY'
import json, sys, urllib.request
host = sys.argv[1]
with urllib.request.urlopen(f'http://{host}:8070/api/status', timeout=3) as response:
    data = json.loads(response.read().decode('utf-8'))
print(json.dumps({
    'scanner_state': data.get('scanner_state'),
    'active_tgid': data.get('active_tgid'),
    'active_talkgroup_label': data.get('active_talkgroup_label'),
    'last_active_tgid': data.get('last_active_tgid'),
    'last_active_talkgroup_label': data.get('last_active_talkgroup_label'),
    'last_active_updated_utc': data.get('last_active_updated_utc'),
    'talkgroup_parser': (data.get('runtime_status') or {}).get('talkgroup_activity_parser'),
}, indent=2, sort_keys=True))
PY
  sleep 3
done
