#!/usr/bin/env bash
set -Eeuo pipefail

PATCH_NAME="deploy_v0_5e1_autostart_rtl_pool"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/${PATCH_NAME}_$(date -u +%Y%m%dT%H%M%SZ).txt"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
log(){ printf '%s\n' "$*" | tee -a "$LOG_FILE"; }
pass(){ PASS_COUNT=$((PASS_COUNT+1)); log "PASS: $*"; }
warn(){ WARN_COUNT=$((WARN_COUNT+1)); log "WARN: $*"; }
fail(){ FAIL_COUNT=$((FAIL_COUNT+1)); log "FAIL: $*"; }
finish(){
  local final="PASS"
  if [[ "$FAIL_COUNT" -gt 0 ]]; then final="FAIL"; fi
  log "UPLOAD_FILE_MSYS=$LOG_FILE"
  local win_path="$LOG_FILE"
  win_path="${win_path#/c/}"
  win_path="C:\\${win_path//\//\\}"
  log "UPLOAD_FILE_WINDOWS=$win_path"
  log "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  log "FINAL: $final"
  [[ "$final" == "PASS" ]]
}
trap 'rc=$?; if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line $LINENO rc=$rc"; fi; finish; exit $rc' EXIT

log "=== Deploy V0.5E1 auto-start scanner/audio and RTL 0000025X guard ==="

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  pass "loaded .env"
else
  warn ".env not found; using shell environment or SSH key auth"
fi

PI_HOST="192.168.254.63"
PI_USER="${PI_USER:-pi}"
PI_ROOT="/home/pi/PI-P25-SCANNER"
pass "target fixed to ${PI_USER}@${PI_HOST}:${PI_ROOT}"

SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=12)
SCP_BASE=(scp -O -o StrictHostKeyChecking=accept-new -o ConnectTimeout=12)
if [[ -n "${PI_PASSWORD:-}" ]]; then
  export SSHPASS="$PI_PASSWORD"
  SSH=(sshpass -e "${SSH_BASE[@]}")
  SCP=(sshpass -e "${SCP_BASE[@]}")
  pass "using sshpass with PI_PASSWORD"
elif [[ -n "${SSHPASS:-}" ]]; then
  SSH=(sshpass -e "${SSH_BASE[@]}")
  SCP=(sshpass -e "${SCP_BASE[@]}")
  pass "using sshpass with SSHPASS"
else
  SSH=("${SSH_BASE[@]}" -o BatchMode=yes)
  SCP=("${SCP_BASE[@]}" -o BatchMode=yes)
  warn "PI_PASSWORD/SSHPASS not set; using non-interactive SSH key auth"
fi

if ! "${SSH[@]}" "${PI_USER}@${PI_HOST}" "test -d '${PI_ROOT}' && test -f '${PI_ROOT}/src/pi_p25_scanner/backend.py'" >>"$LOG_FILE" 2>&1; then
  fail "Pi repo not reachable without interactive prompt; set PI_PASSWORD or SSHPASS in .env/shell"
  exit 1
fi
pass "Pi repo reachable without interactive prompt"

if [[ ! -f web/app.js || ! -f src/pi_p25_scanner/rtl_serial_guard.py ]]; then
  fail "local V0.5E patched files are missing; run the patch script first"
  exit 1
fi

TMP_TAR="runtime/${PATCH_NAME}.tar.gz"
mkdir -p runtime
tar -czf "$TMP_TAR" \
  web/app.js \
  src/pi_p25_scanner/config_store.py \
  src/pi_p25_scanner/backend_launch.py \
  src/pi_p25_scanner/rtl_serial_guard.py
pass "built deploy tar"

"${SCP[@]}" "$TMP_TAR" "${PI_USER}@${PI_HOST}:/tmp/${PATCH_NAME}.tar.gz" >>"$LOG_FILE" 2>&1
pass "copied deploy tar to Pi"

"${SSH[@]}" "${PI_USER}@${PI_HOST}" "PI_ROOT='${PI_ROOT}' PATCH_NAME='${PATCH_NAME}' bash -s" <<'REMOTE_V05E' >>"$LOG_FILE" 2>&1
set -Eeuo pipefail
cd "$PI_ROOT"
mkdir -p runtime/patch_backups/v0_5e1
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
for f in web/app.js src/pi_p25_scanner/config_store.py src/pi_p25_scanner/backend_launch.py; do
  [[ -f "$f" ]] && cp -a "$f" "runtime/patch_backups/v0_5e1/${f//\//_}.${STAMP}.bak"
done
tar -xzf "/tmp/${PATCH_NAME}.tar.gz" -C "$PI_ROOT"
PYTHONPATH="$PI_ROOT/src" python3 -m py_compile \
  src/pi_p25_scanner/config_store.py \
  src/pi_p25_scanner/backend_launch.py \
  src/pi_p25_scanner/rtl_serial_guard.py
PYTHONPATH="$PI_ROOT/src" python3 - <<'REMOTE_PY_V05E'
from __future__ import annotations
import json, re, shutil, time
from pathlib import Path
from pi_p25_scanner.rtl_serial_guard import (
    DEFAULT_CONTROL_SERIAL,
    DEFAULT_VOICE_SERIAL,
    enforce_config_payload_rtl_serial_pool,
    replace_or_add_op25_device_serial,
)
root = Path.cwd()
# Update active runtime config so p25_control/p25_voice are in 0000025X pool.
config_path = root / "runtime" / "settings" / "p25_systems.json"
if config_path.exists():
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    updated = enforce_config_payload_rtl_serial_pool(payload, mutate=False)
    if updated != payload:
        backup_dir = root / "runtime" / "settings" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        shutil.copy2(config_path, backup_dir / f"p25_systems_pre_v0_5e_{stamp}.json")
        config_path.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
# Update validated OP25 marker so stale rtl=00000162 cannot launch.
marker = root / "runtime" / "settings" / "op25_validated_rx_command.env"
if marker.exists():
    lines = marker.read_text(encoding="utf-8").splitlines()
    out = []
    saw_args = False
    for line in lines:
        if line.startswith("P25_VALIDATED_RX_ARGS="):
            key, val = line.split("=", 1)
            quote = ""
            raw = val.strip()
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
                quote = raw[0]
                raw = raw[1:-1]
            raw = replace_or_add_op25_device_serial(raw, DEFAULT_CONTROL_SERIAL)
            out.append(f"{key}={quote}{raw}{quote}")
            saw_args = True
        else:
            out.append(line)
    if not saw_args:
        out.append(f"P25_VALIDATED_RX_ARGS='rtl={DEFAULT_CONTROL_SERIAL}'")
    marker.write_text("\n".join(out) + "\n", encoding="utf-8")
REMOTE_PY_V05E
# Safely restart backend without matching/killing this shell.
if pgrep -f '[p]i_p25_scanner.backend' >/dev/null 2>&1; then
  pkill -TERM -f '[p]i_p25_scanner.backend' || true
  sleep 2
fi
if pgrep -f '[p]i_p25_scanner.backend' >/dev/null 2>&1; then
  pkill -KILL -f '[p]i_p25_scanner.backend' || true
  sleep 1
fi
mkdir -p runtime/logs
nohup env PYTHONPATH="$PI_ROOT/src" python3 -m pi_p25_scanner.backend --host 0.0.0.0 --port 8070 \
  > runtime/logs/backend_v0_5e1.log 2>&1 &
sleep 2
REMOTE_V05E
pass "deployed files, constrained runtime config/marker, restarted backend"

# Verify HTTP and markers.
for i in {1..12}; do
  if curl -fsS "http://${PI_HOST}:8070/api/status" >/tmp/pi_p25_v05e1_status.json 2>>"$LOG_FILE"; then
    break
  fi
  sleep 1
done
if [[ ! -s /tmp/pi_p25_v05e1_status.json ]]; then
  fail "backend status endpoint did not respond after deploy"
  exit 1
fi
pass "backend status endpoint responded"

if ! curl -fsS "http://${PI_HOST}:8070/app.js?v=v0_5e_verify_$(date +%s)" | grep -q "V0_5E_AUTO_START_RTL_POOL"; then
  fail "V0.5E auto-start marker was not served by /app.js"
  exit 1
fi
pass "verified V0.5E marker is served by /app.js"

"${SSH[@]}" "${PI_USER}@${PI_HOST}" "PI_ROOT='${PI_ROOT}' python3 - <<'REMOTE_CHECK_V05E'
from __future__ import annotations
import json, re, sys
from pathlib import Path
root = Path('/home/pi/PI-P25-SCANNER')
config = root / 'runtime' / 'settings' / 'p25_systems.json'
marker = root / 'runtime' / 'settings' / 'op25_validated_rx_command.env'
allowed = re.compile(r'^0000025\\d$')
problems = []
if config.exists():
    data = json.loads(config.read_text(encoding='utf-8'))
    for system in data.get('systems', []):
        roles = system.get('receiver_roles', {}) if isinstance(system, dict) else {}
        for role_name in ('p25_control', 'p25_voice'):
            serial = str((roles.get(role_name) or {}).get('rtl_serial', '')).strip()
            if serial and not allowed.fullmatch(serial):
                problems.append(f'{role_name} serial outside pool: {serial}')
if marker.exists():
    text = marker.read_text(encoding='utf-8')
    m = re.search(r'P25_VALIDATED_RX_ARGS=.*?rtl=([^,\\s\"\']+)', text)
    if m and not allowed.fullmatch(m.group(1)):
        problems.append(f'marker rtl serial outside pool: {m.group(1)}')
if problems:
    print('\n'.join(problems))
    sys.exit(1)
print('RTL serial pool check passed')
REMOTE_CHECK_V05E" >>"$LOG_FILE" 2>&1
pass "verified runtime config and validated marker only reference 0000025X serials"

pass "V0.5E deploy complete"
