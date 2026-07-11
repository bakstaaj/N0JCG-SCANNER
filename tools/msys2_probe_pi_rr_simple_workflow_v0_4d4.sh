#!/usr/bin/env bash
set -Eeuo pipefail
if [[ -f .env ]]; then set -a; source .env; set +a; fi
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
if [[ -n "${SSHPASS:-}" && $(command -v sshpass || true) ]]; then
  SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
elif [[ -n "${PI_PASSWORD:-}" && $(command -v sshpass || true) ]]; then
  export SSHPASS="$PI_PASSWORD"
  SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
else
  SSH_BASE+=( -o BatchMode=yes )
fi
STATE="${1:-AZ}"; COUNTY="${2:-Maricopa}"; CITY="${3:-Mesa}"
"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "cd '$PI_REPO' && python3 - <<PY
import json, urllib.parse, urllib.request
for path in ['/api/status', '/api/radioreference/status']:
    with urllib.request.urlopen('http://127.0.0.1:8070'+path, timeout=10) as r:
        print('===== '+path+' =====')
        print(json.dumps(json.loads(r.read().decode()), indent=2, sort_keys=True)[:4000])
params=urllib.parse.urlencode({'state':'$STATE','county':'$COUNTY','city':'$CITY'})
with urllib.request.urlopen('http://127.0.0.1:8070/api/radioreference/systems?'+params, timeout=30) as r:
    payload=json.loads(r.read().decode())
print('===== /api/radioreference/systems =====')
print(json.dumps({'ok':payload.get('ok'), 'picker_parser':payload.get('picker_parser'), 'state_id':payload.get('state_id'), 'county_id':payload.get('county_id'), 'system_count':payload.get('system_count'), 'systems':payload.get('systems', [])[:12]}, indent=2, sort_keys=True))
PY"
