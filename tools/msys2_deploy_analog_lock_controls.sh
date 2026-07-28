#!/usr/bin/env bash
# Deploy and verify analog Skip, Block, Clear Lock, and Clear Blocks controls.

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
ANALOG_ROOT="${ANALOG_ROOT:-/home/pi/PI-SCANNER}"
P25_ROOT="${P25_ROOT:-/home/pi/PI-P25-SCANNER}"
LOCAL_ARCHIVE="${TMPDIR:-/tmp}/pi-scanner-analog-lock-controls.tar.gz"
REMOTE_ARCHIVE="/tmp/pi-scanner-analog-lock-controls.tar.gz"
PYTHON_BIN="${PYTHON_BIN:-/ucrt64/bin/python.exe}"

pass() { printf 'PASS: %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
cleanup() { rm -f "$LOCAL_ARCHIVE"; }
trap cleanup EXIT

[[ -f "$ENV_FILE" ]] || fail "missing environment file: $ENV_FILE"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${PI_USER:?PI_USER missing from .env}"
: "${PI_HOST:?PI_HOST missing from .env}"
: "${PI_PASSWORD:?PI_PASSWORD missing from .env}"
export SSHPASS="$PI_PASSWORD"

for command_name in sshpass ssh scp tar; do
  command -v "$command_name" >/dev/null 2>&1 \
    || fail "missing MSYS2 command: $command_name"
done
[[ -x "$PYTHON_BIN" ]] || fail "missing UCRT64 Python: $PYTHON_BIN"

cd "$ROOT"
export PYTHONPATH="$ROOT/src"

"$PYTHON_BIN" -m pytest -q \
  tests/test_analog_lock_controls.py \
  tests/test_analog_live_controls_v110.py \
  tests/test_analog_control_state_v112.py \
  tests/test_bottom_analog_controls_v111.py \
  tests/test_squelch_value_layout_v114.py \
  tests/test_browser_control_fix_v117.py \
  tests/test_analog_subprocess_io.py \
  tests/test_vhf_fft_scanner.py
"$PYTHON_BIN" -m py_compile \
  src/pi_p25_scanner/backend.py \
  src/pi_p25_scanner/analog_continuous_scanner.py \
  src/pi_p25_scanner/vhf_fft_scanner.py
/ucrt64/bin/node.exe --check web/app.js
bash -n tools/msys2_deploy_analog_lock_controls.sh
git --no-pager diff --check
pass "local control validation passed"

rm -f "$LOCAL_ARCHIVE"
tar -czf "$LOCAL_ARCHIVE" \
  src/pi_p25_scanner/backend.py \
  src/pi_p25_scanner/analog_continuous_scanner.py \
  src/pi_p25_scanner/vhf_fft_scanner.py \
  web/app.js \
  web/app.css \
  web/index.html \
  docs/VHF_FFT_SCANNER.md
[[ -s "$LOCAL_ARCHIVE" ]] || fail "deployment archive is empty"
pass "deployment archive created"

SSH=(
  sshpass -e ssh
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=12
  "$PI_USER@$PI_HOST"
)
SCP=(
  sshpass -e scp -O
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=12
)

"${SSH[@]}" \
  "test -d '$ANALOG_ROOT' && test -d '$P25_ROOT'" \
  || fail "required Pi application roots are missing"
"${SCP[@]}" \
  "$LOCAL_ARCHIVE" \
  "$PI_USER@$PI_HOST:$REMOTE_ARCHIVE"
pass "deployment archive uploaded"

"${SSH[@]}" \
  "ANALOG_ROOT='$ANALOG_ROOT' P25_ROOT='$P25_ROOT' REMOTE_ARCHIVE='$REMOTE_ARCHIVE' bash -s" \
  <<'REMOTE'
set -Eeuo pipefail

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STAGE="$(mktemp -d /tmp/pi-scanner-analog-controls.XXXXXX)"
BACKUP="$ANALOG_ROOT/runtime/patch_backups/analog_lock_controls_$STAMP"

cleanup() {
  rm -rf -- "$STAGE"
  rm -f -- "$REMOTE_ARCHIVE"
}
trap cleanup EXIT

mkdir -p "$BACKUP/analog/src/pi_p25_scanner"
mkdir -p "$BACKUP/p25/src/pi_p25_scanner" "$BACKUP/p25/web"
cp -a \
  "$ANALOG_ROOT/src/pi_p25_scanner/analog_continuous_scanner.py" \
  "$ANALOG_ROOT/src/pi_p25_scanner/vhf_fft_scanner.py" \
  "$BACKUP/analog/src/pi_p25_scanner/"
cp -a \
  "$P25_ROOT/src/pi_p25_scanner/backend.py" \
  "$BACKUP/p25/src/pi_p25_scanner/"
cp -a \
  "$P25_ROOT/web/app.js" \
  "$P25_ROOT/web/app.css" \
  "$P25_ROOT/web/index.html" \
  "$BACKUP/p25/web/"

tar -xzf "$REMOTE_ARCHIVE" -C "$STAGE"
install -m 0644 \
  "$STAGE/src/pi_p25_scanner/analog_continuous_scanner.py" \
  "$ANALOG_ROOT/src/pi_p25_scanner/analog_continuous_scanner.py"
install -m 0644 \
  "$STAGE/src/pi_p25_scanner/vhf_fft_scanner.py" \
  "$ANALOG_ROOT/src/pi_p25_scanner/vhf_fft_scanner.py"
install -m 0644 \
  "$STAGE/src/pi_p25_scanner/backend.py" \
  "$P25_ROOT/src/pi_p25_scanner/backend.py"
install -m 0644 "$STAGE/web/app.js" "$P25_ROOT/web/app.js"
install -m 0644 "$STAGE/web/app.css" "$P25_ROOT/web/app.css"
install -m 0644 "$STAGE/web/index.html" "$P25_ROOT/web/index.html"

python3 -m py_compile \
  "$ANALOG_ROOT/src/pi_p25_scanner/analog_continuous_scanner.py" \
  "$ANALOG_ROOT/src/pi_p25_scanner/vhf_fft_scanner.py" \
  "$P25_ROOT/src/pi_p25_scanner/backend.py"
if command -v node >/dev/null 2>&1; then
  node --check "$P25_ROOT/web/app.js"
fi

sudo systemctl restart \
  pi-p25-scanner.service \
  pi-scanner-vhf-worker.service \
  pi-scanner-uhf-worker.service

for attempt in 1 2 3 4 5 6 7 8; do
  if systemctl is-active --quiet pi-p25-scanner.service \
    && systemctl is-active --quiet pi-scanner-vhf-worker.service \
    && systemctl is-active --quiet pi-scanner-uhf-worker.service
  then
    break
  fi
  sleep 1
done
systemctl is-active --quiet pi-p25-scanner.service
systemctl is-active --quiet pi-scanner-vhf-worker.service
systemctl is-active --quiet pi-scanner-uhf-worker.service

python3 - "$P25_ROOT" "$ANALOG_ROOT" <<'PY'
import json
import sys
import urllib.request
from pathlib import Path

p25_root = Path(sys.argv[1])
analog_root = Path(sys.argv[2])
html = (p25_root / "web/index.html").read_text(encoding="utf-8")
app = (p25_root / "web/app.js").read_text(encoding="utf-8")
backend = (p25_root / "src/pi_p25_scanner/backend.py").read_text(
    encoding="utf-8"
)
vhf = (analog_root / "src/pi_p25_scanner/vhf_fft_scanner.py").read_text(
    encoding="utf-8"
)
uhf = (
    analog_root / "src/pi_p25_scanner/analog_continuous_scanner.py"
).read_text(encoding="utf-8")

assert 'id="analogClearLockBtn"' in html
assert "analogClearLockBtn: 'clear_lock'" in app
assert 'action == "clear_lock"' in backend
assert "operator_clear_lock" in vhf
assert "operator_clear_lock" in uhf
with urllib.request.urlopen(
    "http://127.0.0.1:8070/api/analog/controls",
    timeout=3,
) as response:
    controls = json.load(response)
assert controls.get("ok") is True
print("deployed_control_contract=PASS")
PY

printf 'backup=%s\n' "$BACKUP"
echo "services=backend:active,vhf:active,uhf:active"
echo "FINAL: PASS"
REMOTE

pass "Pi analog lock controls deployed and verified"
echo "FINAL: PASS"
