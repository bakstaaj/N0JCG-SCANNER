#!/usr/bin/env bash
set -u
PI_HOST="192.168.254.63"
echo "===== /index.html cache marker ====="
curl -fsS "http://${PI_HOST}:8070/index.html?probe=$(date +%s)" | grep -E 'app\.js\?v=0\.5c-unified-radio-setup|app\.js' || true
echo "===== /app.js V0.5C marker ====="
curl -fsS "http://${PI_HOST}:8070/app.js?probe=$(date +%s)" | grep -E 'V0\.5C_UNIFIED_RADIO_SETUP|PI_P25_RADIO_SETUP_VERSION' || true
echo "===== named profiles endpoint ====="
curl -fsS "http://${PI_HOST}:8070/api/config/named" || true
echo
