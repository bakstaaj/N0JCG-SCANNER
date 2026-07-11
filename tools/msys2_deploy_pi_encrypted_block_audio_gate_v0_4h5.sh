#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_NAME="deploy_v0_4h5_encrypted_block_audio_gate"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/${SCRIPT_NAME}_${STAMP}.txt"
PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
exec > >(tee -a "$LOG_FILE") 2>&1
pass(){ echo "PASS: $*"; PASS_COUNT=$((PASS_COUNT+1)); }
warn(){ echo "WARN: $*"; WARN_COUNT=$((WARN_COUNT+1)); }
fail(){ echo "FAIL: $*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
finish(){
  local rc=$?
  if [[ $rc -ne 0 ]]; then fail "deploy aborted unexpectedly at line ${BASH_LINENO[0]} rc=$rc"; fi
  echo "UPLOAD_FILE_MSYS=$LOG_FILE"
  echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"
  echo "SUMMARY: PASS=$PASS_COUNT WARN=$WARN_COUNT FAIL=$FAIL_COUNT"
  if [[ $rc -eq 0 && $FAIL_COUNT -eq 0 ]]; then echo "FINAL: PASS"; else echo "FINAL: FAIL"; fi
  exit $rc
}
trap finish EXIT

echo "=== Deploy V0.4H5 encrypted/blocked TGID audio gate ==="

if [[ ! -f "src/pi_p25_scanner/backend.py" || ! -f "src/pi_p25_scanner/op25_config.py" ]]; then
  echo "ERROR: run this from the PI-P25-SCANNER repo root"
  exit 1
fi
pass "repo root detected: $(pwd)"

PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
if [[ -f .env ]]; then
  # Only consume password/user/repo. Host is intentionally forced to the LAN IP.
  set -a
  # shellcheck disable=SC1091
  source <(grep -E '^(PI_USER|PI_PASSWORD|SSHPASS|PI_REPO)=' .env || true)
  set +a
  PI_HOST="192.168.254.63"
fi
SSHPASS_VALUE="${PI_PASSWORD:-${SSHPASS:-}}"
if [[ -z "$SSHPASS_VALUE" ]]; then
  read -r -s -p "Pi password for ${PI_USER}@${PI_HOST}: " SSHPASS_VALUE
  echo
fi
export SSHPASS="$SSHPASS_VALUE"

for f in src/pi_p25_scanner/backend.py src/pi_p25_scanner/op25_config.py; do
  [[ -f "$f" ]] || { echo "missing deploy file: $f"; exit 1; }
done
pass "deploy prerequisites present"

TARBALL="/tmp/pi_p25_v0_4h5_encrypted_block_audio_gate_${STAMP}.tar.gz"
tar -czf "$TARBALL" src/pi_p25_scanner/backend.py src/pi_p25_scanner/op25_config.py
pass "created deploy tarball: $TARBALL"

sshpass -e scp -O -o StrictHostKeyChecking=accept-new "$TARBALL" "${PI_USER}@${PI_HOST}:/tmp/$(basename "$TARBALL")"
pass "copied deploy tarball to ${PI_HOST}"

sshpass -e ssh -o StrictHostKeyChecking=accept-new "${PI_USER}@${PI_HOST}" "PI_REPO='$PI_REPO' TARBALL='/tmp/$(basename "$TARBALL")' bash -s" <<'REMOTE'
set -Eeuo pipefail
cd "$PI_REPO"
mkdir -p runtime/patch_backups/v0_4h5_deploy
cp -p src/pi_p25_scanner/backend.py "runtime/patch_backups/v0_4h5_deploy/backend.py.$(date -u +%Y%m%dT%H%M%SZ).bak" 2>/dev/null || true
cp -p src/pi_p25_scanner/op25_config.py "runtime/patch_backups/v0_4h5_deploy/op25_config.py.$(date -u +%Y%m%dT%H%M%SZ).bak" 2>/dev/null || true
tar -xzf "$TARBALL"
python3 -m py_compile src/pi_p25_scanner/backend.py src/pi_p25_scanner/op25_config.py
PYTHONPATH=src python3 - <<'PY'
import json
from pi_p25_scanner.config_store import resolve_config_path
from pi_p25_scanner.op25_config import DEFAULT_OUTPUT_DIR, generate_op25_configs
manifest = generate_op25_configs(resolve_config_path(), DEFAULT_OUTPUT_DIR).to_dict()
print("GENERATED_OP25_MANIFEST_BEGIN")
print(json.dumps({"trunk_tsv": manifest.get("trunk_tsv"), "systems": manifest.get("systems", []), "warnings": manifest.get("warnings", [])}, indent=2, sort_keys=True))
print("GENERATED_OP25_MANIFEST_END")
PY
sudo systemctl restart pi-p25-scanner.service
sleep 2
REMOTE
pass "remote files installed, OP25 runtime config regenerated, backend restarted"

python3 - <<'PY'
import json, time, urllib.request
base = "http://192.168.254.63:8070"
for path in ["/api/status", "/api/activity"]:
    last = None
    for _ in range(30):
        try:
            with urllib.request.urlopen(base + path, timeout=2.0) as r:
                data = json.loads(r.read().decode("utf-8"))
            print(f"PROBE_OK {path} state={data.get('scanner_state')} active_tgid={data.get('active_tgid')} blocked_count={(data.get('blocked_talkgroups') or {}).get('count')}")
            break
        except Exception as exc:
            last = exc
            time.sleep(1)
    else:
        raise SystemExit(f"PROBE_FAIL {base + path} {last!r}")
PY
pass "backend status/activity probes passed"

python3 - <<'PY'
import json, urllib.request
try:
    with urllib.request.urlopen("http://192.168.254.63:8072/api/audio/status", timeout=2.0) as r:
        data = json.loads(r.read().decode("utf-8"))
    print(f"AUDIO_BRIDGE_OK gate_path={data.get('gate_path')} mode={data.get('mode')} log_gate_events={data.get('log_gate_events')}")
except Exception as exc:
    print(f"AUDIO_BRIDGE_WARN {exc!r}")
PY
pass "audio bridge status checked"

echo "Dashboard: http://192.168.254.63:8070"
