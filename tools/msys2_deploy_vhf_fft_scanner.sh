#!/usr/bin/env bash
# Deploy and hardware-validate the FFT-directed VHF scanner from MSYS2 UCRT64.

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/../PI-P25-SCANNER/.env}"
ANALOG_ROOT="${ANALOG_ROOT:-/home/pi/PI-SCANNER}"
P25_ROOT="${P25_ROOT:-/home/pi/PI-P25-SCANNER}"
REMOTE_ARCHIVE="/tmp/pi-scanner-vhf-fft-deploy.tar.gz"
LOCAL_ARCHIVE="${TMPDIR:-/tmp}/pi-scanner-vhf-fft-deploy.tar.gz"
PASS_COUNT=0
FAIL_COUNT=0

pass() { printf 'PASS: %s\n' "$*"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { printf 'FAIL: %s\n' "$*" >&2; FAIL_COUNT=$((FAIL_COUNT + 1)); }

finish() {
  rm -f "$LOCAL_ARCHIVE"
  printf 'SUMMARY: PASS=%d FAIL=%d\n' "$PASS_COUNT" "$FAIL_COUNT"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    printf 'FINAL: PASS\n'
  else
    printf 'FINAL: FAIL\n'
  fi
}
trap finish EXIT
trap 'FAIL_COUNT=$((FAIL_COUNT + 1))' ERR

[[ -f "$ENV_FILE" ]] || { fail "missing environment file: $ENV_FILE"; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${PI_USER:?PI_USER missing from .env}"
: "${PI_HOST:?PI_HOST missing from .env}"
: "${PI_PASSWORD:?PI_PASSWORD missing from .env}"
export SSHPASS="$PI_PASSWORD"

for command_name in sshpass ssh scp tar python3; do
  command -v "$command_name" >/dev/null 2>&1 \
    || { fail "missing local command: $command_name"; exit 1; }
done
pass "local deployment commands available"

cd "$ROOT"
export PYTHONPATH="$ROOT/src"

python3 -m pi_p25_scanner.analog_vhf_worker --self-test >/dev/null
python3 -m unittest discover -s tests -p 'test_vhf_fft_scanner.py' >/dev/null
python3 -m py_compile \
  src/pi_p25_scanner/vhf_fft_scanner.py \
  src/pi_p25_scanner/analog_vhf_worker.py \
  src/pi_p25_scanner/analog_channels.py
bash -n tools/pi5_vhf_phase_smoke.sh
git --no-pager diff --check
pass "local VHF validation passed"

rm -f "$LOCAL_ARCHIVE"
tar -czf "$LOCAL_ARCHIVE" \
  src/pi_p25_scanner/vhf_fft_scanner.py \
  src/pi_p25_scanner/analog_vhf_worker.py \
  src/pi_p25_scanner/analog_channels.py \
  config/analog_receivers.example.json \
  config/receiver_roles.example.json \
  systemd/pi-scanner-vhf-worker.service \
  systemd/pi-scanner-vhf-worker.service.d/10-usbfs-memory.conf \
  tools/pi5_vhf_phase_smoke.sh \
  docs/VHF_FFT_SCANNER.md
[[ -s "$LOCAL_ARCHIVE" ]] \
  || { fail "deployment archive is empty"; exit 1; }
pass "deployment archive created"

SSH=(
  sshpass -e ssh
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=10
  "${PI_USER}@${PI_HOST}"
)
SCP=(
  sshpass -e scp -O
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=10
)

"${SSH[@]}" \
  "test -d '$ANALOG_ROOT' && test -d '$P25_ROOT/src/pi_p25_scanner'" \
  || { fail "required Pi application directories are missing"; exit 1; }
pass "Pi application directories verified"

"${SCP[@]}" \
  "$LOCAL_ARCHIVE" \
  "${PI_USER}@${PI_HOST}:$REMOTE_ARCHIVE"
pass "deployment archive uploaded"

"${SSH[@]}" \
  "ANALOG_ROOT='$ANALOG_ROOT' P25_ROOT='$P25_ROOT' REMOTE_ARCHIVE='$REMOTE_ARCHIVE' bash -s" \
  <<'REMOTE'
set -Eeuo pipefail

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STAGE="$(mktemp -d /tmp/pi-scanner-vhf-fft.XXXXXX)"
BACKUP="$ANALOG_ROOT/runtime/patch_backups/vhf_fft_rebuild_$STAMP"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE="pi-scanner-vhf-worker.service"
DEPLOYED=0

cleanup() {
  rm -rf "$STAGE"
  rm -f "$REMOTE_ARCHIVE"
}

rollback() {
  rc=$?
  if [[ "$DEPLOYED" -eq 1 ]]; then
    printf 'FAIL: deployment failed; restoring %s\n' "$BACKUP" >&2
    sudo systemctl stop "$SERVICE" >/dev/null 2>&1 || true
    while IFS= read -r relative; do
      [[ -n "$relative" ]] || continue
      if [[ -f "$BACKUP/$relative" ]]; then
        install -D -m 0644 "$BACKUP/$relative" "$ANALOG_ROOT/$relative"
      fi
    done < "$BACKUP/analog_existing_files.txt"
    if [[ -f "$BACKUP/p25_analog_channels.py" ]]; then
      install -m 0644 \
        "$BACKUP/p25_analog_channels.py" \
        "$P25_ROOT/src/pi_p25_scanner/analog_channels.py"
    fi
    if [[ -f "$BACKUP/pi-scanner-vhf-worker.service" ]]; then
      sudo install -m 0644 \
        "$BACKUP/pi-scanner-vhf-worker.service" \
        "$SYSTEMD_DIR/pi-scanner-vhf-worker.service"
    fi
    if [[ -f "$BACKUP/90-persistent-fft.conf" ]]; then
      sudo install -D -m 0644 \
        "$BACKUP/90-persistent-fft.conf" \
        "$SYSTEMD_DIR/pi-scanner-vhf-worker.service.d/90-persistent-fft.conf"
    fi
    sudo systemctl daemon-reload
    sudo systemctl restart "$SERVICE" >/dev/null 2>&1 || true
    sudo systemctl restart pi-p25-scanner.service >/dev/null 2>&1 || true
  fi
  cleanup
  exit "$rc"
}
trap rollback ERR
trap cleanup EXIT

mkdir -p "$BACKUP"
tar -xzf "$REMOTE_ARCHIVE" -C "$STAGE"

python3 - <<PY
import json
from pathlib import Path

path = Path("$ANALOG_ROOT/runtime/settings/analog_receivers.json")
payload = json.loads(path.read_text(encoding="utf-8"))
workers = payload.get("workers", {})
expected = {"analog_2m": "00000144", "analog_70cm": "00000440"}
for role, serial in expected.items():
    actual = str((workers.get(role) or {}).get("rtl_serial") or "")
    if actual != serial:
        raise SystemExit(f"{role} must use {serial}; found {actual!r}")
if not any(
    item.get("enabled", True)
    for item in (workers.get("analog_2m") or {}).get("channels", [])
):
    raise SystemExit("runtime VHF channel list is empty")
print("PASS: runtime serial bindings and VHF channel list")
PY

paths=(
  src/pi_p25_scanner/analog_vhf_worker.py
  src/pi_p25_scanner/analog_channels.py
  src/pi_p25_scanner/persistent_vhf_fft_scanner.py
  config/analog_receivers.example.json
  config/receiver_roles.example.json
  tools/pi5_vhf_phase_smoke.sh
)
: > "$BACKUP/analog_existing_files.txt"
for relative in "${paths[@]}"; do
  if [[ -f "$ANALOG_ROOT/$relative" ]]; then
    printf '%s\n' "$relative" >> "$BACKUP/analog_existing_files.txt"
    install -D -m 0644 "$ANALOG_ROOT/$relative" "$BACKUP/$relative"
  fi
done
cp -p \
  "$P25_ROOT/src/pi_p25_scanner/analog_channels.py" \
  "$BACKUP/p25_analog_channels.py"
sudo cp -p \
  "$SYSTEMD_DIR/pi-scanner-vhf-worker.service" \
  "$BACKUP/pi-scanner-vhf-worker.service"
if [[ -f "$SYSTEMD_DIR/pi-scanner-vhf-worker.service.d/90-persistent-fft.conf" ]]; then
  sudo cp -p \
    "$SYSTEMD_DIR/pi-scanner-vhf-worker.service.d/90-persistent-fft.conf" \
    "$BACKUP/90-persistent-fft.conf"
fi

DEPLOYED=1
sudo systemctl stop "$SERVICE"

install -D -m 0644 \
  "$STAGE/src/pi_p25_scanner/vhf_fft_scanner.py" \
  "$ANALOG_ROOT/src/pi_p25_scanner/vhf_fft_scanner.py"
install -D -m 0644 \
  "$STAGE/src/pi_p25_scanner/analog_vhf_worker.py" \
  "$ANALOG_ROOT/src/pi_p25_scanner/analog_vhf_worker.py"
install -D -m 0644 \
  "$STAGE/src/pi_p25_scanner/analog_channels.py" \
  "$ANALOG_ROOT/src/pi_p25_scanner/analog_channels.py"
install -D -m 0644 \
  "$STAGE/src/pi_p25_scanner/analog_channels.py" \
  "$P25_ROOT/src/pi_p25_scanner/analog_channels.py"
install -D -m 0644 \
  "$STAGE/config/analog_receivers.example.json" \
  "$ANALOG_ROOT/config/analog_receivers.example.json"
install -D -m 0644 \
  "$STAGE/config/receiver_roles.example.json" \
  "$ANALOG_ROOT/config/receiver_roles.example.json"
install -D -m 0755 \
  "$STAGE/tools/pi5_vhf_phase_smoke.sh" \
  "$ANALOG_ROOT/tools/pi5_vhf_phase_smoke.sh"
install -D -m 0644 \
  "$STAGE/docs/VHF_FFT_SCANNER.md" \
  "$ANALOG_ROOT/docs/VHF_FFT_SCANNER.md"
rm -f "$ANALOG_ROOT/src/pi_p25_scanner/persistent_vhf_fft_scanner.py"

sudo install -m 0644 \
  "$STAGE/systemd/pi-scanner-vhf-worker.service" \
  "$SYSTEMD_DIR/pi-scanner-vhf-worker.service"
sudo install -D -m 0644 \
  "$STAGE/systemd/pi-scanner-vhf-worker.service.d/10-usbfs-memory.conf" \
  "$SYSTEMD_DIR/pi-scanner-vhf-worker.service.d/10-usbfs-memory.conf"
sudo rm -f \
  "$SYSTEMD_DIR/pi-scanner-vhf-worker.service.d/90-persistent-fft.conf"

cd "$ANALOG_ROOT"
export PYTHONPATH="$ANALOG_ROOT/src"
python3 -m py_compile \
  src/pi_p25_scanner/vhf_fft_scanner.py \
  src/pi_p25_scanner/analog_vhf_worker.py \
  src/pi_p25_scanner/analog_channels.py
python3 -m pi_p25_scanner.analog_vhf_worker --self-test
bash -n tools/pi5_vhf_phase_smoke.sh
python3 -m json.tool config/analog_receivers.example.json >/dev/null

sudo systemctl daemon-reload
bash tools/pi5_vhf_phase_smoke.sh

sudo systemctl restart "$SERVICE"
sudo systemctl restart pi-p25-scanner.service
sleep 4
systemctl is-active --quiet "$SERVICE"
systemctl is-active --quiet pi-p25-scanner.service

python3 - <<PY
import json
import time
import urllib.request
from pathlib import Path

status_path = Path("$ANALOG_ROOT/runtime/status/analog_2m.json")
deadline = time.time() + 12
status = {}
while time.time() < deadline:
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        status = {}
    if (
        status.get("rtl_serial") == "00000144"
        and status.get("search_mode") == "fft_directed_nfm_v2"
        and status.get("state") not in {"error", "stopped", None}
    ):
        break
    time.sleep(0.5)
else:
    raise SystemExit(f"VHF service status validation failed: {status}")

with urllib.request.urlopen(
    "http://127.0.0.1:8070/api/analog/channels", timeout=5
) as response:
    channels = json.loads(response.read().decode("utf-8"))
bindings = channels.get("serial_bindings") or {}
if bindings.get("analog_2m") != "00000144":
    raise SystemExit(f"web API VHF binding is wrong: {bindings}")
if bindings.get("analog_70cm") != "00000440":
    raise SystemExit(f"web API UHF binding is wrong: {bindings}")
if int((channels.get("enabled_counts") or {}).get("analog_2m") or 0) < 1:
    raise SystemExit(f"web API has no uploaded VHF channels: {channels}")

print("PASS: VHF service FFT state and web channel API")
PY

trap - ERR
DEPLOYED=0
printf 'BACKUP_PATH=%s\n' "$BACKUP"
printf 'FINAL: PASS\n'
REMOTE

pass "Pi deployment, hardware smoke, and API verification passed"
