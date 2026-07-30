#!/usr/bin/env bash
set -Eeuo pipefail

export PATH=/ucrt64/bin:/usr/bin:/bin
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[[ -f .env ]] || { printf 'FAIL: missing %s/.env\n' "$ROOT" >&2; exit 1; }
set -a
# shellcheck disable=SC1091
source .env
set +a

: "${PI_USER:?PI_USER missing from .env}"
: "${PI_HOST:?PI_HOST missing from .env}"
: "${PI_PASSWORD:?PI_PASSWORD missing from .env}"
export SSHPASS="$PI_PASSWORD"

python - <<'PY'
import csv
from pathlib import Path

expected = {
    ("2m", 146.970, "K0ESD"),
    ("70cm", 448.450, "K0ESD"),
    ("70cm", 449.325, "KA4EPS"),
    ("70cm", 449.700, "KC0CVU"),
}
for path in (
    Path("config/analog_channels_cabin.csv"),
    Path("config/channel_lists/analog_channels_cabin.csv"),
):
    with path.open(newline="", encoding="utf-8") as handle:
        actual = {
            (row["receiver"], float(row["frequency_mhz"]), row["name"])
            for row in csv.DictReader(handle)
        }
    if not expected <= actual:
        raise SystemExit(f"FAIL: requested repeaters missing from {path}")
print("PASS: local cabin channel lists contain all requested repeaters")
PY

sshpass -e ssh \
  -o StrictHostKeyChecking=accept-new \
  -o ConnectTimeout=10 \
  "${PI_USER}@${PI_HOST}" bash -s <<'REMOTE'
set -Eeuo pipefail

root=/home/pi/PI-SCANNER
config="$root/runtime/settings/analog_receivers.json"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="$root/runtime/patch_backups/add_k0esd_kc0cvu_ka4eps_${stamp}"
mkdir -p "$backup"
cp -p "$config" "$backup/analog_receivers.json"

cd "$root"
PYTHONPATH="$root/src" python3 - "$config" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
requested_frequencies = {
    "analog_2m": [(146_970_000, "K0ESD")],
    "analog_70cm": [
        (448_450_000, "K0ESD"),
        (449_325_000, "KA4EPS"),
        (449_700_000, "KC0CVU"),
    ],
}
requested = {}
for role, entries in requested_frequencies.items():
    requested[role] = [
        {
            "id": f"{role}-{frequency}-{name.lower()}",
            "enabled": True,
            "name": name,
            "frequency_hz": frequency,
            "mode": "nfm",
            "priority": 0,
            "gain_db": 40.2,
            "squelch_rms": 1800,
            "hold_seconds": 0.9,
            "resume_delay_seconds": 1.2,
            "ctcss_hz": None,
            "tone_gate": False,
            "dcs_code": "",
            "dcs_gate": False,
            "recording_enabled": False,
        }
        for frequency, name in entries
    ]
workers = payload.setdefault("workers", {})

for role, additions in requested.items():
    channels = workers.setdefault(role, {}).setdefault("channels", [])
    by_frequency = {int(item.get("frequency_hz", 0)): item for item in channels}
    for addition in additions:
        frequency = int(addition["frequency_hz"])
        existing = by_frequency.get(frequency)
        if existing is None:
            channels.append(addition)
            by_frequency[frequency] = addition
        else:
            existing.update(addition)
    channels.sort(key=lambda item: (int(item.get("frequency_hz", 0)), str(item.get("name", ""))))

expected_serials = {"analog_2m": "00000144", "analog_70cm": "00000440"}
for role, serial in expected_serials.items():
    actual = str(workers.get(role, {}).get("rtl_serial", ""))
    if actual != serial:
        raise SystemExit(f"FAIL: {role} serial changed: expected {serial}, found {actual}")

temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
os.replace(temporary, path)
print("PASS: runtime analog configuration updated atomically")
PY

sudo systemctl restart pi-scanner-vhf-worker.service
sudo systemctl restart pi-scanner-uhf-worker.service

for service in pi-scanner-vhf-worker.service pi-scanner-uhf-worker.service; do
  for attempt in $(seq 1 20); do
    systemctl is-active --quiet "$service" && break
    sleep 1
  done
  systemctl is-active --quiet "$service"
done

python3 - "$config" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "analog_2m": {146_970_000: "K0ESD"},
    "analog_70cm": {
        448_450_000: "K0ESD",
        449_325_000: "KA4EPS",
        449_700_000: "KC0CVU",
    },
}
for role, frequencies in expected.items():
    channels = payload["workers"][role]["channels"]
    actual = {int(item["frequency_hz"]): item["name"] for item in channels}
    for frequency, name in frequencies.items():
        if actual.get(frequency) != name:
            raise SystemExit(f"FAIL: {role} missing {name} at {frequency}")
print("PASS: all four repeaters are active in the runtime channel lists")
PY

echo "BACKUP=$backup"
echo "FINAL: PASS"
REMOTE
