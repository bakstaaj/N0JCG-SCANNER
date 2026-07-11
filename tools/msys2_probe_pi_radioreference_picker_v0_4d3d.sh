#!/usr/bin/env bash
set -Eeuo pipefail
STATE="${1:-AZ}"
COUNTY="${2:-Maricopa}"
CITY="${3:-Mesa}"
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [[ -n "${PI_PASSWORD:-${SSHPASS:-}}" ]] && command -v sshpass >/dev/null 2>&1; then
  SSHPASS_VALUE="${PI_PASSWORD:-${SSHPASS:-}}"
  SSH=(sshpass -p "$SSHPASS_VALUE" ssh "${SSH_OPTS[@]}")
else
  SSH=(ssh "${SSH_OPTS[@]}")
fi
"${SSH[@]}" "$PI_USER@$PI_HOST" STATE="$STATE" COUNTY="$COUNTY" CITY="$CITY" python3 - <<'PYREMOTE'
import json, os, urllib.request
payload = {"state": os.environ.get("STATE", "AZ"), "county": os.environ.get("COUNTY", "Maricopa"), "city": os.environ.get("CITY", "Mesa"), "categories": ["Fire", "EMS", "Law Enforcement", "Interop"]}
body = json.dumps(payload).encode()
req = urllib.request.Request('http://127.0.0.1:8070/api/radioreference/systems', data=body, headers={'Content-Type':'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=45) as response:
    systems = json.loads(response.read().decode('utf-8', 'replace'))
print(json.dumps(systems, indent=2, sort_keys=True))
first = None
for item in systems.get('systems', []):
    if item.get('system_id'):
        first = item['system_id']
        break
if first:
    body = json.dumps({'system_id': first}).encode()
    req = urllib.request.Request('http://127.0.0.1:8070/api/radioreference/sites', data=body, headers={'Content-Type':'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=45) as response:
        sites = json.loads(response.read().decode('utf-8', 'replace'))
    print('--- sites for first system ---')
    print(json.dumps(sites, indent=2, sort_keys=True))
PYREMOTE
