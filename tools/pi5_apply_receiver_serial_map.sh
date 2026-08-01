#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MODE=dry-run
CONFIRMED=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      MODE=dry-run
      ;;
    --apply)
      MODE=apply
      ;;
    --yes)
      CONFIRMED=true
      ;;
    -h|--help)
      echo "Usage: $0 [--dry-run] [--apply --yes]"
      exit 0
      ;;
    *)
      echo "FAIL: unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

if [[ "$MODE" == apply && "$CONFIRMED" != true ]]; then
  echo "FAIL: --apply requires --yes" >&2
  exit 2
fi

cd "$ROOT"
export PYTHONPATH="$ROOT/src"

python3 - "$MODE" <<'PY'
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from pi_p25_scanner.receiver_inventory import validate_receiver_role_payload

mode = sys.argv[1]
root = Path.cwd()
path = root / "runtime" / "settings" / "receiver_roles.json"
template = root / "config" / "receiver_roles.example.json"

canonical = {
    "p25_control": {
        "label": "P25 Control",
        "service": "p25",
        "rtl_serial": "00000251",
        "enabled": True,
        "protected": True,
        "notes": "Dedicated P25 control-channel receiver",
    },
    "p25_voice": {
        "label": "P25 Voice",
        "service": "p25",
        "rtl_serial": "00000252",
        "enabled": True,
        "protected": True,
        "notes": "Dedicated P25 voice-follow receiver",
    },
    "analog_2m": {
        "label": "Analog 2 m",
        "service": "analog",
        "rtl_serial": "00000144",
        "enabled": True,
        "protected": False,
        "notes": "Operational VHF FFT scanner",
    },
    "analog_70cm": {
        "label": "Analog 70 cm",
        "service": "analog",
        "rtl_serial": "00000440",
        "enabled": True,
        "protected": False,
        "notes": "Operational UHF FFT scanner",
    },
}

print("Canonical PI Scanner RTL-SDR role map:")
for role, entry in canonical.items():
    print(f"  {role:14s} {entry['rtl_serial']}  {entry['label']}")

if mode == "dry-run":
    print(f"DRY_RUN: no changes made; target={path}")
    raise SystemExit(0)

if path.exists():
    payload = json.loads(path.read_text(encoding="utf-8"))
elif template.exists():
    payload = json.loads(template.read_text(encoding="utf-8"))
else:
    raise SystemExit(f"FAIL: neither {path} nor {template} exists")

payload["schema_version"] = max(1, int(payload.get("schema_version") or 1))
roles = payload.setdefault("roles", {})
for role, entry in canonical.items():
    roles[role] = entry
validate_receiver_role_payload(payload)

path.parent.mkdir(parents=True, exist_ok=True)
backup = None
if path.exists():
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"receiver_roles_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    shutil.copy2(path, backup)

temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
os.replace(temporary, path)
print(f"APPLIED={path}")
if backup:
    print(f"BACKUP={backup}")
print("FINAL: PASS")
PY
