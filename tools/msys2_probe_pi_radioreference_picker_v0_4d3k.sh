#!/usr/bin/env bash
set -Eeuo pipefail
STATE="${1:-AZ}"
COUNTY="${2:-Maricopa}"
CITY="${3:-Mesa}"
HOST="192.168.254.63"
BASE="http://${HOST}:8070"
json_escape(){ python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }
payload="{\"state\":$(json_escape "$STATE"),\"county\":$(json_escape "$COUNTY"),\"city\":$(json_escape "$CITY")}"
echo "===== /api/radioreference/status ====="
curl -sS "$BASE/api/radioreference/status" | python3 -m json.tool || true
echo "===== /api/radioreference/systems ====="
body="$(curl -sS -X POST "$BASE/api/radioreference/systems" -H 'Content-Type: application/json' --data "$payload")"
printf '%s\n' "$body" | python3 -m json.tool || printf '%s\n' "$body"
system_id="$(printf '%s' "$body" | python3 -c 'import json,sys; data=json.load(sys.stdin); systems=data.get("systems") or []; print(systems[0].get("system_id") if systems else "")' 2>/dev/null || true)"
if [[ -n "$system_id" ]]; then
  echo "===== /api/radioreference/sites system_id=$system_id ====="
  curl -sS -X POST "$BASE/api/radioreference/sites" -H 'Content-Type: application/json' --data "{\"system_id\":$system_id}" | python3 -m json.tool || true
fi
