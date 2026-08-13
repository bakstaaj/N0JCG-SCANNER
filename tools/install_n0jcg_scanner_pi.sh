#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ENV_FILE="$ROOT/.env"
die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
for command_name in sshpass ssh scp git tar; do command -v "$command_name" >/dev/null || die "missing MSYS command: $command_name"; done

printf 'N0JCG Scanner | New Raspberry Pi installation\n\n'
read -r -p 'Pi IP address: ' PI_HOST
read -r -p 'SSH user [pi]: ' PI_USER; PI_USER="${PI_USER:-pi}"
read -r -s -p 'SSH password: ' PI_PASSWORD; printf '\n'
PI_REPO="/home/${PI_USER}/n0jcg-scanner"
[[ "$PI_HOST" =~ ^[A-Za-z0-9_.:-]+$ ]] || die 'invalid host'
[[ "$PI_USER" =~ ^[A-Za-z0-9_-]+$ ]] || die 'invalid user'

umask 077; touch "$ENV_FILE"
grep -vE '^(PI_HOST|PI_USER|PI_PASSWORD|PI_REPO|RADIO_HOST|RADIO_USER|RADIO_PASSWORD|RADIO_REPO)=' "$ENV_FILE" > "$ENV_FILE.tmp" || true
{
  cat "$ENV_FILE.tmp"
  printf 'PI_HOST=%q\nPI_USER=%q\nPI_PASSWORD=%q\nPI_REPO=%q\nRADIO_HOST=%q\nRADIO_USER=%q\nRADIO_PASSWORD=%q\nRADIO_REPO=%q\n' "$PI_HOST" "$PI_USER" "$PI_PASSWORD" "$PI_REPO" "$PI_HOST" "$PI_USER" "$PI_PASSWORD" "$PI_REPO"
} > "$ENV_FILE.new"
mv "$ENV_FILE.new" "$ENV_FILE"; rm -f "$ENV_FILE.tmp"
printf 'Saved reusable settings to %s (keep private).\n' "$ENV_FILE"

export SSHPASS="$PI_PASSWORD"
TARGET="$PI_USER@$PI_HOST"
SSH=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "$TARGET")
SCP=(scp -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
sshpass -e "${SSH[@]}" true || die 'SSH connection failed'

printf '\n[1/5] Installing OS components; services remain stopped\n'
printf '%s\n' "$PI_PASSWORD" | sshpass -e "${SSH[@]}" sudo -S -p '' apt-get update
printf '%s\n' "$PI_PASSWORD" | sshpass -e "${SSH[@]}" sudo -S -p '' apt-get install -y --no-install-recommends python3 python3-pip python3-venv python3-numpy python3-scipy rtl-sdr librtlsdr-dev gr-osmosdr sox netcat-openbsd usbutils git

printf '\n[2/5] Deploying repository to %s\n' "$PI_REPO"
git archive --format=tar --prefix=n0jcg-scanner/ HEAD | gzip -n > /tmp/n0jcg-scanner-install.tar.gz
sshpass -e "${SCP[@]}" /tmp/n0jcg-scanner-install.tar.gz "$TARGET:/tmp/n0jcg-scanner-install.tar.gz"
sshpass -e "${SSH[@]}" sh -s -- "$PI_REPO" "$PI_USER" "$PI_PASSWORD" <<'REMOTE_INSTALL'
set -eu
repo="$1"; user="$2"; sudo_password="$3"
as_root() { printf '%s\n' "$sudo_password" | sudo -S -p '' "$@"; }
mkdir -p "$repo"
tar -xzf /tmp/n0jcg-scanner-install.tar.gz --strip-components=1 -C "$repo"
mkdir -p "$repo"/runtime/{logs,status,settings,op25,patch_backups,settings/configs}
chown -R "$user:$user" "$repo" || true
rm -f /tmp/n0jcg-scanner-install.tar.gz
for unit in "$repo"/systemd/*.service; do
  [ -f "$unit" ] || continue
  sed "s#User=pi#User=$user#g; s#Group=pi#Group=$user#g; s#/home/pi/n0jcg-scanner#$repo#g" "$unit" | as_root tee "/etc/systemd/system/$(basename "$unit")" >/dev/null
done
for dropin in "$repo"/systemd/*.service.d/*.conf; do
  [ -f "$dropin" ] || continue
  name="$(basename "$(dirname "$dropin")")"; as_root mkdir -p "/etc/systemd/system/$name"
  sed "s#/home/pi/n0jcg-scanner#$repo#g" "$dropin" | as_root tee "/etc/systemd/system/$name/$(basename "$dropin")" >/dev/null
done
as_root systemctl daemon-reload
for unit in pi-p25-scanner pi-p25-audio-pool pi-p25-raw-audio-bridge pi-scanner-vhf-worker pi-scanner-vhf-audio pi-scanner-uhf-worker pi-scanner-uhf-audio; do as_root systemctl disable --now "$unit.service" 2>/dev/null || true; done
REMOTE_INSTALL

printf '\n[3/5] Assign RTL-SDR serials one at a time\n'
declare -A SERIALS
for role in p25_control p25_voice analog_2m analog_70cm; do
  case "$role" in
    p25_control) label='P25 Control SDR'; serial='00000251' ;;
    p25_voice) label='P25 Voice SDR'; serial='00000252' ;;
    analog_2m) label='VHF / 2 m SDR'; serial='00000144' ;;
    analog_70cm) label='UHF / 70 cm SDR'; serial='00000440' ;;
  esac
  printf '\nInsert the %s, and make sure no other RTL-SDR is connected.\n' "$label"; read -r -p 'Press Enter when ready: '
  SERIALS[$role]="$serial"
  printf '%s\n' "$PI_PASSWORD" | sshpass -e "${SSH[@]}" sudo -S -p '' rtl_eeprom -d 0 -s "$serial" || die "rtl_eeprom failed for $role"
  assigned="$(sshpass -e "${SSH[@]}" rtl_eeprom -d 0 2>/dev/null | tr -d '\r' || true)"
  printf '%s\n' "$assigned" | grep -Fq "$serial" || die "could not verify assigned serial $serial for $role"
  printf 'PASS: %s assigned serial %s.\n' "$label" "$serial"
  printf 'Remove the %s before continuing.\n' "$label"; read -r -p 'Press Enter after removal: '
done
sshpass -e "${SSH[@]}" sh -s -- "$PI_REPO" "${SERIALS[p25_control]}" "${SERIALS[p25_voice]}" "${SERIALS[analog_2m]}" "${SERIALS[analog_70cm]}" <<'REMOTE_ROLES'
set -eu
repo="$1"; control="$2"; voice="$3"; vhf="$4"; uhf="$5"
python3 - "$repo" "$control" "$voice" "$vhf" "$uhf" <<'PY'
import json, sys
repo, control, voice, vhf, uhf = sys.argv[1:]
payload = {"schema_version": 1, "roles": {
  "p25_control": {"label": "P25 Control", "service": "p25", "rtl_serial": control, "enabled": True, "protected": True},
  "p25_voice": {"label": "P25 Voice", "service": "p25", "rtl_serial": voice, "enabled": True, "protected": True},
  "analog_2m": {"label": "Analog 2 m", "service": "analog", "rtl_serial": vhf, "enabled": True, "protected": False},
  "analog_70cm": {"label": "Analog 70 cm", "service": "analog", "rtl_serial": uhf, "enabled": True, "protected": False}}}
with open(f"{repo}/runtime/settings/receiver_roles.json", "w", encoding="utf-8") as stream:
    json.dump(payload, stream, indent=2); stream.write("\n")
PY
REMOTE_ROLES

printf '\n[4/5] Validating without starting services\n'
sshpass -e "${SSH[@]}" sh -s -- "$PI_REPO" <<'REMOTE_VALIDATE'
set -eu
repo="$1"
command -v rtl_test >/dev/null
python3 -m py_compile "$repo"/src/pi_p25_scanner/*.py
python3 -m json.tool "$repo/runtime/settings/receiver_roles.json" >/dev/null
test -f "$repo/systemd/pi-p25-scanner.service"
echo 'PASS: RTL tools, Python modules, role map, and service files validated.'
REMOTE_VALIDATE

printf '\n[5/5] Complete. Services are intentionally stopped.\n'
printf 'Load a profile through Radio setup, then start services after confirming the role map.\n'
printf 'Target: http://%s:8070/\n' "$PI_HOST"
rm -f /tmp/n0jcg-scanner-install.tar.gz
