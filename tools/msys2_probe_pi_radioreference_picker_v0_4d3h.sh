#!/usr/bin/env bash
set -Eeuo pipefail

STATE="${1:-AZ}"
COUNTY="${2:-Maricopa}"
CITY="${3:-Mesa}"
PI_HOST="192.168.254.63"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/probe_v0_4d3h_radioreference_picker_${STAMP}.txt"
mkdir -p "$LOG_DIR" 2>/dev/null || true
exec > >(tee -a "$LOG_FILE") 2>&1

json_escape(){ python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }
BODY="{\"state\":$(json_escape "$STATE"),\"county\":$(json_escape "$COUNTY"),\"city\":$(json_escape "$CITY")}"

for endpoint in status systems; do
  if [[ "$endpoint" == "status" ]]; then
    url="http://${PI_HOST}:8070/api/radioreference/status"
    code="$(curl -sS --max-time 15 -o /tmp/pi_p25_rr_${endpoint}_v0_4d3h.json -w '%{http_code}' "$url" || true)"
  else
    url="http://${PI_HOST}:8070/api/radioreference/systems"
    code="$(curl -sS --max-time 45 -o /tmp/pi_p25_rr_${endpoint}_v0_4d3h.json -w '%{http_code}' -H 'Content-Type: application/json' -d "$BODY" "$url" || true)"
  fi
  echo "===== ${url} HTTP ${code} ====="
  cat /tmp/pi_p25_rr_${endpoint}_v0_4d3h.json 2>/dev/null || true
  echo
  echo "===== END ${url} ====="
done

echo "UPLOAD_FILE_MSYS=$LOG_FILE"
echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"
echo "FINAL: PASS"
