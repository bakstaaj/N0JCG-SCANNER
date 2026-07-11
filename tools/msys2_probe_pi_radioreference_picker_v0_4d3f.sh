#!/usr/bin/env bash
set -Eeuo pipefail
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
STATE="${1:-AZ}"
COUNTY="${2:-Maricopa}"
CITY="${3:-Mesa}"
SSH=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if command -v sshpass >/dev/null 2>&1; then
  if [[ -n "${PI_PASSWORD:-}" ]]; then
    export SSHPASS="$PI_PASSWORD"
  elif [[ -n "${SSHPASS:-}" ]]; then
    export SSHPASS="$SSHPASS"
  else
    read -r -s -p "Pi password for ${PI_USER}@${PI_HOST}: " PI_PASSWORD_INPUT
    echo
    export SSHPASS="$PI_PASSWORD_INPUT"
  fi
  SSH=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
fi
"${SSH[@]}" "$PI_USER@$PI_HOST" "python3 - <<'PY'
import json
import urllib.error
import urllib.request
state='$STATE'
county='$COUNTY'
city='$CITY'

def post(path, payload):
    req=urllib.request.Request('http://127.0.0.1:8070'+path, data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')

def get(path):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8070'+path, timeout=30) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')

for label, call in [
    ('/api/radioreference/status', lambda: get('/api/radioreference/status')),
    ('/api/radioreference/systems', lambda: post('/api/radioreference/systems', {'state':state,'county':county,'city':city})),
]:
    code, body = call()
    print(f'===== {label} HTTP {code} =====')
    print(body)
    print(f'===== END {label} =====')
PY"
