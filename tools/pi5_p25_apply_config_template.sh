#!/usr/bin/env bash
# Apply a PI-P25-SCANNER JSON config template on the Raspberry Pi.
set -Eeuo pipefail

TEMPLATE_PATH=""
REPO="$(pwd -P)"
YES=0
GENERATE=1
RESTART_BACKEND=0

usage() {
  cat <<USAGE
Usage:
  ./tools/pi5_p25_apply_config_template.sh --template /path/to/template.json --yes [--repo /home/pi/PI-P25-SCANNER] [--no-generate] [--restart-backend]

Copies a validated template into runtime/settings/p25_systems.json, backs up the previous runtime config, and regenerates OP25 runtime files.
USAGE
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --template) shift; TEMPLATE_PATH="$1"; shift ;;
    --repo) shift; REPO="$1"; shift ;;
    --yes) YES=1; shift ;;
    --no-generate) GENERATE=0; shift ;;
    --restart-backend) RESTART_BACKEND=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$YES" -ne 1 ]]; then
  printf 'FAIL: --yes is required\n' >&2
  exit 2
fi
if [[ -z "$TEMPLATE_PATH" || ! -f "$TEMPLATE_PATH" ]]; then
  printf 'FAIL: template file missing: %s\n' "$TEMPLATE_PATH" >&2
  exit 2
fi
if [[ ! -d "$REPO" ]]; then
  printf 'FAIL: repo directory missing: %s\n' "$REPO" >&2
  exit 2
fi

cd "$REPO"
if [[ ! -d src/pi_p25_scanner ]]; then
  printf 'FAIL: not a PI-P25-SCANNER repo: %s\n' "$REPO" >&2
  exit 2
fi

PYTHONPATH="$REPO/src" python3 - "$TEMPLATE_PATH" "$GENERATE" <<'PY'
import json
import sys
from pathlib import Path

from pi_p25_scanner.config_store import RUNTIME_CONFIG_PATH, write_runtime_config
from pi_p25_scanner.op25_config import DEFAULT_OUTPUT_DIR, generate_op25_configs

source = Path(sys.argv[1])
generate = sys.argv[2] == "1"
payload = json.loads(source.read_text(encoding="utf-8"))
result = write_runtime_config(payload, backup=True)
print("PASS: wrote runtime config", result["config_path"])
if result.get("backup_path"):
    print("PASS: backed up previous runtime config", result["backup_path"])
validation = result["validation"]
first = validation["first_enabled_system"]
print("PASS: validated system", first.get("name"), "talkgroups", len(first.get("talkgroups", [])))
if generate:
    manifest = generate_op25_configs(RUNTIME_CONFIG_PATH, DEFAULT_OUTPUT_DIR)
    data = manifest.to_dict()
    print("PASS: generated OP25 trunk config", data["trunk_tsv"])
    for system in data.get("systems", []):
        print("PASS: OP25 system", system.get("name"), "enabled_talkgroups", system.get("talkgroup_count"))
    for warning in data.get("warnings", []):
        print("WARN:", warning)
PY

if [[ "$RESTART_BACKEND" -eq 1 ]]; then
  if systemctl list-unit-files pi-p25-scanner.service >/dev/null 2>&1; then
    if [[ "$(id -u)" -eq 0 ]]; then
      systemctl restart pi-p25-scanner.service
    elif sudo -n true >/dev/null 2>&1; then
      sudo systemctl restart pi-p25-scanner.service
    elif [[ -n "${SUDO_PASSWORD:-}" ]]; then
      printf '%s\n' "$SUDO_PASSWORD" | sudo -S systemctl restart pi-p25-scanner.service
    else
      sudo systemctl restart pi-p25-scanner.service
    fi
    printf 'PASS: restarted pi-p25-scanner.service\n'
  else
    printf 'WARN: pi-p25-scanner.service unit not found; backend not restarted\n'
  fi
fi

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [[ -n "$LAN_IP" ]]; then
  printf 'APP_URL=http://%s:8070\n' "$LAN_IP"
else
  printf 'APP_URL=http://<pi-ip>:8070\n'
fi
printf 'FINAL: PASS\n'
