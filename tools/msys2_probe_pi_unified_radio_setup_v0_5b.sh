#!/usr/bin/env bash
set -Eeuo pipefail
if [[ -f .env ]]; then set -a; source .env || true; set +a; fi
PI_HOST="192.168.254.63"
echo "===== /api/config/named ====="
curl -fsS "http://${PI_HOST}:8070/api/config/named" | python3 -m json.tool || true
echo "===== /api/radioreference/status ====="
curl -fsS "http://${PI_HOST}:8070/api/radioreference/status" | python3 -m json.tool || true
echo "===== app.js marker ====="
curl -fsS "http://${PI_HOST}:8070/app.js" | grep -n 'V0.5B_RADIO_SETUP_UNIFIED' || true
