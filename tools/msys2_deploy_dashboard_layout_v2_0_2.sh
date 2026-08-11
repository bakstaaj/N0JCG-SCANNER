#!/usr/bin/env bash
# Deploy the compact PI Scanner dashboard layout without restarting RF workers.

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
P25_ROOT="${P25_ROOT:-/home/pi/n0jcg-scanner}"
LOCAL_ARCHIVE="${TMPDIR:-/tmp}/pi-scanner-dashboard-v2.0.2.tar.gz"
REMOTE_ARCHIVE="/tmp/pi-scanner-dashboard-v2.0.2.tar.gz"

pass() { printf 'PASS: %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
cleanup() { rm -f -- "$LOCAL_ARCHIVE"; }
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

cd "$ROOT"
export PYTHONPATH="$ROOT/src"
python -m pytest -q \
  tests/test_audio_arbitrator_status_v203.py \
  tests/test_compact_800x480_v115.py \
  tests/test_responsive_top_row_v116.py \
  tests/test_squelch_value_layout_v114.py
node --check web/app.js
git --no-pager diff --check
pass "local dashboard validation passed"

rm -f -- "$LOCAL_ARCHIVE"
tar -czf "$LOCAL_ARCHIVE" web/index.html web/app.css web/app.js

sshpass -e scp -O \
  -o StrictHostKeyChecking=accept-new \
  -o ConnectTimeout=12 \
  "$LOCAL_ARCHIVE" \
  "$PI_USER@$PI_HOST:$REMOTE_ARCHIVE"

sshpass -e ssh \
  -o StrictHostKeyChecking=accept-new \
  -o ConnectTimeout=12 \
  "$PI_USER@$PI_HOST" \
  "P25_ROOT='$P25_ROOT' REMOTE_ARCHIVE='$REMOTE_ARCHIVE' bash -s" <<'REMOTE'
set -Eeuo pipefail

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STAGE="$(mktemp -d /tmp/pi-scanner-dashboard.XXXXXX)"
BACKUP="$P25_ROOT/runtime/patch_backups/dashboard_layout_v2_0_2_$STAMP"

cleanup() {
  rm -rf -- "$STAGE"
  rm -f -- "$REMOTE_ARCHIVE"
}
trap cleanup EXIT

mkdir -p "$BACKUP/web"
cp -a \
  "$P25_ROOT/web/index.html" \
  "$P25_ROOT/web/app.css" \
  "$P25_ROOT/web/app.js" \
  "$BACKUP/web/"
tar -xzf "$REMOTE_ARCHIVE" -C "$STAGE"
install -m 0644 "$STAGE/web/index.html" "$P25_ROOT/web/index.html"
install -m 0644 "$STAGE/web/app.css" "$P25_ROOT/web/app.css"
install -m 0644 "$STAGE/web/app.js" "$P25_ROOT/web/app.js"
if command -v node >/dev/null 2>&1; then
  node --check "$P25_ROOT/web/app.js"
fi

python3 - "$P25_ROOT" <<'PY'
import sys
import urllib.request
from pathlib import Path

root = Path(sys.argv[1])
html = (root / "web/index.html").read_text(encoding="utf-8")
css = (root / "web/app.css").read_text(encoding="utf-8")
app = (root / "web/app.js").read_text(encoding="utf-8")

assert "<title>PI Scanner</title>" in html
assert 'id="dashboardSummary"' not in html
assert html.index('id="stateBadge"') < html.index('id="connectionStatus"')
assert 'id="analogSquelchValue"' not in html
assert 'id="analogClearLockBtn"' in html
assert "PI_SCANNER_DASHBOARD_LAYOUT_V202" in css
assert "renderAudioArbitratorStatus" in app

with urllib.request.urlopen("http://127.0.0.1:8070/", timeout=5) as response:
    live = response.read().decode("utf-8")
assert "<title>PI Scanner</title>" in live
print("live_dashboard_contract=PASS")
PY

printf 'backup=%s\n' "$BACKUP"
echo "FINAL: PASS"
REMOTE

pass "Pi dashboard layout deployed and verified"
echo "FINAL: PASS"
