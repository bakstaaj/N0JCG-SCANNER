#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if [[ -f .env ]]; then set -a; # shellcheck disable=SC1091
  source .env; set +a; fi
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
SYSTEM_ID="${1:-2082}"
SITE_ID="${2:-}"
SSH_BASE=(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes)
if command -v sshpass >/dev/null 2>&1 && [[ -n "${SSHPASS:-${PI_PASSWORD:-}}" ]]; then
  export SSHPASS="${SSHPASS:-$PI_PASSWORD}"
  SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
fi
"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "python3 - <<PY
import json, urllib.request, urllib.parse
system_id='${SYSTEM_ID}'
site_id='${SITE_ID}'
base='http://127.0.0.1:8070'
print('===== /api/radioreference/sites =====')
url=base + '/api/radioreference/sites?system_id=' + urllib.parse.quote(system_id)
print(json.dumps(json.load(urllib.request.urlopen(url, timeout=20)), indent=2, sort_keys=True))
print('===== import parser marker from source =====')
text=open('/home/pi/PI-P25-SCANNER/src/pi_p25_scanner/radioreference_import.py', encoding='utf-8').read()
print('explicit-site-frequency-v0.4d5' in text)
if site_id:
    print('NOTE: This probe does not import automatically. Use the UI Import and Save button for system', system_id, 'site', site_id)
PY"
