#!/usr/bin/env bash
set -Eeuo pipefail

export PATH=/ucrt64/bin:/usr/bin:/bin
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
set -a
[[ -f .env ]] && . ./.env
set +a

roc_host="${ROC_HOST:-192.168.68.114}"
radio_host="${RADIO_HOST:-${PI_HOST:-192.168.68.137}}"
failures=0

check_json() {
  local label="$1"
  local url="$2"
  if payload="$(curl -fsS --connect-timeout 3 --max-time 10 "$url")" \
    && python -m json.tool >/dev/null 2>&1 <<<"$payload"; then
    printf 'PASS: %s (%s)\n' "$label" "$url"
  else
    printf 'FAIL: %s (%s)\n' "$label" "$url" >&2
    failures=$((failures + 1))
  fi
}

check_json "ROC dashboard health" "http://$roc_host:8095/api/health"
check_json "radio Pi API" "http://$radio_host:8070/api/status"
check_json "radio Pi audio fanout" "http://$radio_host:8072/api/audio/status"

if scanner_page="$(curl -fsS --connect-timeout 3 --max-time 10 "http://$radio_host:8070/")" \
  && grep -q 'N0JCG Scanner' <<<"$scanner_page"; then
  printf 'PASS: radio Pi scanner web application\n'
else
  printf 'FAIL: radio Pi scanner web application\n' >&2
  failures=$((failures + 1))
fi

if roc_apps="$(curl -fsS --connect-timeout 3 --max-time 10 "http://$roc_host:8095/api/applications")" \
  && RADIO_HOST_CHECK="$radio_host" RADIO_APPS="$roc_apps" python - <<'PY'
import json, os, sys
try:
    apps = json.loads(os.environ["RADIO_APPS"])
    scanner = next(item for item in apps["applications"] if item.get("id") == "scanner")
    ok = scanner.get("host") == os.environ["RADIO_HOST_CHECK"] and int(scanner.get("port")) == 8070
except (KeyError, TypeError, ValueError, StopIteration, json.JSONDecodeError):
    ok = False
sys.exit(0 if ok else 1)
PY
then
  printf 'PASS: ROC dashboard links to radio Pi\n'
else
  printf 'FAIL: ROC dashboard radio Pi link\n' >&2
  failures=$((failures + 1))
fi

if ((failures)); then
  printf 'FINAL=FAIL (%s checks failed)\n' "$failures" >&2
  exit 1
fi
printf 'FINAL=PASS\n'
