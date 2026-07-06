#!/usr/bin/env bash
set -Eeuo pipefail

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
  ./tools/msys2_deploy_pi_app_audio_integration.sh [options]

Options:
  --host HOST        Pi host name or pi@HOST. Default: PI-SDR
  --user USER        Pi SSH user. Default: pi
  --repo PATH        Pi repo path. Default: /home/pi/PI-P25-SCANNER
  --password PASS    Pi password for sshpass. Prefer PI_PASSWORD in .env.
  -h, --help         Show this help

Pulls latest code on the Pi, validates the raw audio bridge/backend syntax,
and restarts pi-p25-scanner.service.
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

REMOTE_SCRIPT='set -Eeuo pipefail
cd "$1"
git pull
python3 -m py_compile src/pi_p25_scanner/backend.py tools/pi5_p25_browser_audio_raw_bridge_server.py
python3 tools/pi5_p25_browser_audio_raw_bridge_server.py --self-test
sudo systemctl restart pi-p25-scanner.service
sleep 2
systemctl is-active --quiet pi-p25-scanner.service
printf "FINAL: PASS\n"
'

sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=accept-new "${PI_USER}@${PI_HOST}" \
  "bash -s -- '$PI_REPO'" <<< "$REMOTE_SCRIPT"
