#!/usr/bin/env bash
set -Eeuo pipefail
STATE="${1:-AZ}"
COUNTY="${2:-Maricopa}"
CITY="${3:-Mesa}"
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
[ -f .env ] && set -a && . ./.env && set +a || true
PI_HOST="192.168.254.63"
SSH_BASE=(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [ -n "${SSHPASS:-}" ] || [ -n "${PI_PASSWORD:-}" ]; then
  command -v sshpass >/dev/null 2>&1 || { echo "FAIL: sshpass missing"; exit 1; }
  export SSHPASS="${SSHPASS:-${PI_PASSWORD:-}}"
  SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
fi
"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "python3 - <<'PY'
import json, urllib.request
state=${STATE@Q}; county=${COUNTY@Q}; city=${CITY@Q}
for endpoint, payload in [
    ('/api/radioreference/status', None),
    ('/api/radioreference/systems', {'state': state, 'county': county, 'city': city}),
]:
    if payload is None:
        req=urllib.request.Request('http://127.0.0.1:8070'+endpoint, method='GET')
    else:
        req=urllib.request.Request('http://127.0.0.1:8070'+endpoint, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=35) as r:
            print('===== '+endpoint+' HTTP '+str(r.status)+' =====')
            print(r.read().decode())
    except Exception as e:
        print('===== '+endpoint+' ERROR =====')
        print(e)
PY"
