#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-PI-SDR}"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
PI_PASSWORD_ARG=""

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a
  . ./.env
  set +a
fi
if [[ -f ./tools/msys2_env_common.sh ]]; then
  # shellcheck disable=SC1091
  . ./tools/msys2_env_common.sh >/dev/null 2>&1 || true
fi

usage() {
  cat <<'USAGE'
Usage:
  ./tools/msys2_deploy_pi_audio_url_host_fix.sh [options]

Options:
  --host HOST       Pi host name or pi@HOST. Default: PI-SDR
  --user USER       SSH user. Default: pi
  --repo PATH       Pi repo path. Default: /home/pi/PI-P25-SCANNER
  --password PASS   Pi password for sshpass. Prefer PI_PASSWORD in .env.
  -h, --help        Show help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) shift; PI_HOST="$1"; shift ;;
    --user) shift; PI_USER="$1"; shift ;;
    --repo) shift; PI_REPO="$1"; shift ;;
    --password) shift; PI_PASSWORD_ARG="$1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "FAIL: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$PI_HOST" == *@* ]]; then
  PI_USER="${PI_HOST%@*}"
  PI_HOST="${PI_HOST#*@}"
fi
if ! command -v sshpass >/dev/null 2>&1; then
  echo "FAIL: sshpass is required in MSYS2" >&2
  exit 1
fi
if [[ -n "$PI_PASSWORD_ARG" ]]; then
  PI_PASSWORD="$PI_PASSWORD_ARG"
elif [[ -n "${PI_PASSWORD:-}" ]]; then
  PI_PASSWORD="$PI_PASSWORD"
elif [[ -n "${SSHPASS:-}" ]]; then
  PI_PASSWORD="$SSHPASS"
else
  read -r -s -p "Pi password for ${PI_USER}@${PI_HOST}: " PI_PASSWORD
  echo
fi
if [[ -z "$PI_PASSWORD" ]]; then
  echo "FAIL: empty Pi password" >&2
  exit 1
fi

REMOTE_SCRIPT='set -euo pipefail
cd "$1"
printf "=== Deploy PI-P25 V0.3P audio URL host fix ===\n"
git pull
python3 -m py_compile src/pi_p25_scanner/backend.py
printf "%s\n" "$2" | sudo -S systemctl restart pi-p25-scanner.service >/tmp/pi_p25_audio_fix_sudo.log 2>&1 || {
  cat /tmp/pi_p25_audio_fix_sudo.log
  exit 1
}
sleep 2
if systemctl is-active --quiet pi-p25-scanner.service; then
  echo "PASS: service active: pi-p25-scanner.service"
else
  echo "FAIL: service is not active: pi-p25-scanner.service"
  systemctl --no-pager --full status pi-p25-scanner.service || true
  exit 1
fi
LAN_IP="$(hostname -I 2>/dev/null | awk '\''{print $1}'\'' || true)"
if [[ -z "$LAN_IP" ]]; then LAN_IP="$1"; fi
printf "APP_URL=http://%s:8070\n" "$LAN_IP"
printf "AUDIO_STREAM_EXPECTED=http://%s:8072/audio.wav\n" "$LAN_IP"
python3 - <<PY
import json
import urllib.request
for url in ("http://127.0.0.1:8070/api/status", "http://127.0.0.1:8070/audio_host_fix.js"):
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            print(f"PASS: {url} -> HTTP {resp.status}")
    except Exception as exc:
        print(f"FAIL: {url} -> {exc}")
        raise SystemExit(1)
PY
'

sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=accept-new "${PI_USER}@${PI_HOST}" \
  "bash -s -- '$PI_REPO' '$PI_PASSWORD'" <<< "$REMOTE_SCRIPT"
