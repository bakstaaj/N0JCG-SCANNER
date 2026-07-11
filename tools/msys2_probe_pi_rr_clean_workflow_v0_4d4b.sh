#!/usr/bin/env bash
set -Eeuo pipefail
if [[ -f .env ]]; then set -a; source .env; set +a; fi
PI_USER="${PI_USER:-pi}"; PI_HOST="192.168.254.63"
SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
if [[ -n "${SSHPASS:-}" || -n "${PI_PASSWORD:-}" ]]; then
  export SSHPASS="${SSHPASS:-${PI_PASSWORD:-}}"
  SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
else
  SSH_BASE+=( -o BatchMode=yes )
fi
"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "python3 - <<'PY'
import json, urllib.parse, urllib.request
base='http://127.0.0.1:8070'
for path in ['/api/status','/api/radioreference/status','/api/radioreference/systems']:
    try:
        if path.endswith('/systems'):
            data=json.dumps({'state':'AZ','county':'Maricopa','city':'Mesa'}).encode()
            req=urllib.request.Request(base+path, data=data, headers={'Content-Type':'application/json'}, method='POST')
            raw=urllib.request.urlopen(req, timeout=25).read().decode()
        else:
            raw=urllib.request.urlopen(base+path, timeout=10).read().decode()
        print('===== '+path+' =====')
        print(json.dumps(json.loads(raw), indent=2, sort_keys=True))
    except Exception as exc:
        print('===== '+path+' ERROR =====')
        print(type(exc).__name__+': '+str(exc))
PY"
