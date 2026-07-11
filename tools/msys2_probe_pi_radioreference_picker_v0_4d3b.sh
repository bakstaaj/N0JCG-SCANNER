#!/usr/bin/env bash
set -Eeuo pipefail
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
STATE="${1:-AZ}"
COUNTY="${2:-Maricopa}"
CITY="${3:-Mesa}"
if [[ -n "${PI_PASSWORD:-${SSHPASS:-}}" ]] && command -v sshpass >/dev/null 2>&1; then
  SSH=(sshpass -p "${PI_PASSWORD:-${SSHPASS:-}}" ssh -o StrictHostKeyChecking=accept-new)
else
  SSH=(ssh -o StrictHostKeyChecking=accept-new)
fi
"${SSH[@]}" "${PI_USER}@${PI_HOST}" "python3 - <<PY
import json, urllib.request
payload=json.dumps({'state':'$STATE','county':'$COUNTY','city':'$CITY'}).encode()
req=urllib.request.Request('http://127.0.0.1:8070/api/radioreference/systems', data=payload, headers={'Content-Type':'application/json'}, method='POST')
try:
    with urllib.request.urlopen(req, timeout=45) as r:
        systems=json.loads(r.read().decode())
    print(json.dumps({'ok': systems.get('ok'), 'searched': systems.get('searched'), 'system_count': systems.get('system_count'), 'systems': systems.get('systems', [])[:15]}, indent=2))
except Exception as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}, indent=2))
PY"
