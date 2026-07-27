#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(
  cd "$(dirname "${BASH_SOURCE[0]}")/.." &&
  pwd
)"

P25_ROOT="${P25_ROOT:-/home/pi/PI-P25-SCANNER}"
ANALOG_ROOT="${ANALOG_ROOT:-/home/pi/PI-SCANNER}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

if [[ "${EUID}" -ne 0 ]]; then
  fail "run with sudo or as root"
fi

for unit in \
  pi-p25-raw-audio-bridge.service \
  pi-p25-audio-pool.service \
  pi-scanner-vhf-worker.service \
  pi-scanner-uhf-worker.service
do
  test -f "$REPO_ROOT/systemd/$unit" \
    || fail "missing systemd/$unit"

  install -m 0644 \
    "$REPO_ROOT/systemd/$unit" \
    "$SYSTEMD_DIR/$unit"
done

install -m 0644 \
  "$REPO_ROOT/web/audio_arbitrator_live.js" \
  "$P25_ROOT/web/audio_arbitrator_live.js"

install -m 0644 \
  "$REPO_ROOT/web/index.html" \
  "$P25_ROOT/web/index.html"

chown pi:pi \
  "$P25_ROOT/web/audio_arbitrator_live.js" \
  "$P25_ROOT/web/index.html"

rm -rf \
  "$SYSTEMD_DIR/pi-p25-raw-audio-bridge.service.d" \
  "$SYSTEMD_DIR/pi-p25-audio-pool.service.d"

systemctl daemon-reload

systemctl disable --now \
  pi-scanner-vhf-audio.service \
  pi-scanner-uhf-audio.service \
  2>/dev/null || true

systemctl enable \
  pi-p25-raw-audio-bridge.service \
  pi-p25-audio-pool.service \
  pi-scanner-vhf-worker.service \
  pi-scanner-uhf-worker.service

systemctl restart pi-p25-raw-audio-bridge.service
systemctl restart pi-p25-audio-pool.service
systemctl restart pi-scanner-vhf-worker.service
systemctl restart pi-scanner-uhf-worker.service
systemctl restart pi-p25-scanner.service

for unit in \
  pi-p25-raw-audio-bridge.service \
  pi-p25-audio-pool.service \
  pi-scanner-vhf-worker.service \
  pi-scanner-uhf-worker.service \
  pi-p25-scanner.service
do
  systemctl is-active --quiet "$unit" \
    || fail "$unit is not active"
done

for unit in \
  pi-scanner-vhf-audio.service \
  pi-scanner-uhf-audio.service
do
  if systemctl is-active --quiet "$unit"; then
    fail "$unit is unexpectedly active"
  fi
done

ARBITRATOR_AFTER="$(
  systemctl show \
    pi-p25-raw-audio-bridge.service \
    -p After --value
)"

[[ "$ARBITRATOR_AFTER" != *"pi-p25-audio-pool.service"* ]] \
  || fail "arbitrator dependency cycle remains"

PORTS="$(
  ss -lunpt |
  grep -E ':(23456|23458|23459|2350[0-9]|8072)\b' \
  || true
)"

for port in 23456 23458 23459 8072; do
  [[ "$PORTS" == *":$port"* ]] \
    || fail "required audio port $port is not listening"
done

curl -fsS --max-time 5 \
  http://127.0.0.1:8072/api/audio/status \
  >/dev/null \
  || fail "audio arbitrator API unavailable"

echo "FINAL=PASS"
