#!/usr/bin/env bash
set -Eeuo pipefail
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
STATE="${1:-AZ}"
COUNTY="${2:-Maricopa}"
CITY="${3:-Mesa}"
SSHPASS_CMD=()
if command -v sshpass >/dev/null 2>&1; then
  if [[ -z "${PI_PASSWORD:-}${SSHPASS:-}" ]]; then
    read -r -s -p "Pi password for ${PI_USER}@${PI_HOST}: " PI_PASSWORD
    echo
  fi
  export SSHPASS="${PI_PASSWORD:-${SSHPASS:-}}"
  SSHPASS_CMD=(sshpass -e)
fi
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
json_payload=$(python3 - <<PY
import json
print(json.dumps({'state': '$STATE', 'county': '$COUNTY', 'city': '$CITY'}))
PY
)
"${SSHPASS_CMD[@]}" ssh "${SSH_OPTS[@]}" "${PI_USER}@${PI_HOST}" "set -Eeuo pipefail
printf '===== /api/radioreference/status =====\n'
curl -sS --max-time 10 http://127.0.0.1:8070/api/radioreference/status | python3 -m json.tool || true
printf '===== /api/radioreference/systems =====\n'
curl -sS --max-time 30 -X POST http://127.0.0.1:8070/api/radioreference/systems -H 'Content-Type: application/json' --data '$json_payload' | python3 -m json.tool || true
"
