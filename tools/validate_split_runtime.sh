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
check_json "ROC proxied radio status" "http://$roc_host:8095/pi-scanner/api/status"
check_json "ROC proxied audio status" "http://$roc_host:8095/pi-scanner/audio-api/api/audio/status"
check_json "radio Pi API" "http://$radio_host:8070/api/status"
check_json "radio Pi audio fanout" "http://$radio_host:8072/api/audio/status"

if scanner_page="$(curl -fsS --connect-timeout 3 --max-time 10 "http://$roc_host:8095/pi-scanner/")" \
  && grep -q '3.0.1-roc-subpath' <<<"$scanner_page"; then
  printf 'PASS: ROC PI Scanner web assets\n'
else
  printf 'FAIL: ROC PI Scanner web assets\n' >&2
  failures=$((failures + 1))
fi

if ((failures)); then
  printf 'FINAL=FAIL (%s checks failed)\n' "$failures" >&2
  exit 1
fi
printf 'FINAL=PASS\n'
