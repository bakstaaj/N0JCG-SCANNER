#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && set -a && source .env && set +a || true
STATE="${1:-AZ}"; COUNTY="${2:-Maricopa}"; CITY="${3:-Mesa}"
PI_USER="${PI_USER:-pi}"; PI_HOST="192.168.254.63"; REMOTE="${PI_USER}@${PI_HOST}"
SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
if [[ -n "${SSHPASS:-}" && -x "$(command -v sshpass)" ]]; then
  SSH_CMD=(sshpass -e "${SSH_BASE[@]}")
elif [[ -n "${PI_PASSWORD:-}" && -x "$(command -v sshpass)" ]]; then
  export SSHPASS="$PI_PASSWORD"; SSH_CMD=(sshpass -e "${SSH_BASE[@]}")
else
  SSH_CMD=("${SSH_BASE[@]}" -o BatchMode=yes)
fi
"${SSH_CMD[@]}" "$REMOTE" "python3 - '$STATE' '$COUNTY' '$CITY' <<'PY'
import json, sys, urllib.request, urllib.error
state, county, city = sys.argv[1:4]
base='http://127.0.0.1:8070'
def post(path, payload):
    req=urllib.request.Request(base+path, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
print('===== /api/radioreference/status =====')
print(urllib.request.urlopen(base+'/api/radioreference/status', timeout=10).read().decode())
print('===== /api/radioreference/systems =====')
status, body = post('/api/radioreference/systems', {'state':state,'county':county,'city':city})
print('HTTP', status)
print(body)
try:
    p=json.loads(body)
    systems=p.get('systems') or []
    if systems:
        sid=systems[0].get('system_id')
        print('===== /api/radioreference/sites first system =====')
        s2,b2=post('/api/radioreference/sites', {'system_id':sid})
        print('HTTP', s2)
        print(b2)
except Exception as exc:
    print('SITE_PROBE_SKIPPED', type(exc).__name__, exc)
PY"
