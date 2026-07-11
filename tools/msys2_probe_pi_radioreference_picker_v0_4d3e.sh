#!/usr/bin/env bash
set -Eeuo pipefail
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
STATE="${1:-AZ}"
COUNTY="${2:-Maricopa}"
CITY="${3:-Mesa}"
SSH=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [[ -n "${PI_PASSWORD:-${SSHPASS:-}}" ]] && command -v sshpass >/dev/null 2>&1; then
  SSH=(sshpass -p "${PI_PASSWORD:-$SSHPASS}" ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
fi
"${SSH[@]}" "$PI_USER@$PI_HOST" "python3 - <<'PY'
import json, urllib.request
payload=json.dumps({'state':'$STATE','county':'$COUNTY','city':'$CITY'}).encode()
req=urllib.request.Request('http://127.0.0.1:8070/api/radioreference/systems', data=payload, headers={'Content-Type':'application/json'}, method='POST')
print(urllib.request.urlopen(req, timeout=60).read().decode())
PY"
