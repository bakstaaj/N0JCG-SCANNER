#!/usr/bin/env bash
set -Eeuo pipefail

PI_HOST="${1:-}"
PI_USER="${2:-pi}"
RELEASE_REF="${3:-v1.0.1-rc1}"

REMOTE_APP="/home/${PI_USER}/scanner"
ANALOG_ROOT="/home/${PI_USER}/scanner"
REMOTE_ARCHIVE="/tmp/pi-p25-scanner-${RELEASE_REF}.tar"
REMOTE_STAGE="/tmp/pi-p25-scanner-${RELEASE_REF}-stage"
SERVICE_NAME="pi-p25-scanner.service"
LOCAL_ARCHIVE="/tmp/pi-p25-scanner-${RELEASE_REF}.tar"

if [[ -z "$PI_HOST" ]]; then
    echo "Usage: $0 <pi-ip-or-hostname> [pi-user] [release-ref]"
    exit 2
fi

for tool in git ssh scp; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "FAIL: required local tool not found: $tool"
        exit 1
    fi
done

if ! git rev-parse --verify "${RELEASE_REF}^{commit}" >/dev/null 2>&1; then
    echo "FAIL: release reference not found: $RELEASE_REF"
    exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
    echo "FAIL: local working tree is not clean"
    git status --short
    exit 1
fi

RELEASE_COMMIT="$(git rev-parse "${RELEASE_REF}^{commit}")"

echo "=== PI P25 Scanner RC deployment ==="
echo "HOST=$PI_HOST"
echo "USER=$PI_USER"
echo "RELEASE_REF=$RELEASE_REF"
echo "RELEASE_COMMIT=$RELEASE_COMMIT"
echo "P25_APP=$REMOTE_APP"
echo "ANALOG_ROOT=$ANALOG_ROOT"

echo
echo "=== Local validation ==="

PYTHONPATH=src python3 -m compileall -q src/pi_p25_scanner
PYTHONPATH=src python3 -m pytest -q
git --no-pager diff --check

echo "PASS: local validation"

echo
echo "=== Creating release archive ==="

rm -f "$LOCAL_ARCHIVE"

git archive \
    --format=tar \
    --output="$LOCAL_ARCHIVE" \
    "$RELEASE_REF"

test -s "$LOCAL_ARCHIVE"
echo "PASS: created $LOCAL_ARCHIVE"

echo
echo "=== Copying release archive ==="

scp -O \
    "$LOCAL_ARCHIVE" \
    "${PI_USER}@${PI_HOST}:${REMOTE_ARCHIVE}"

echo "PASS: archive copied"

echo
echo "=== Deploying release candidate ==="

ssh -t "${PI_USER}@${PI_HOST}" \
    "PI_USER='$PI_USER' \
     REMOTE_APP='$REMOTE_APP' \
     ANALOG_ROOT='$ANALOG_ROOT' \
     REMOTE_ARCHIVE='$REMOTE_ARCHIVE' \
     REMOTE_STAGE='$REMOTE_STAGE' \
     RELEASE_REF='$RELEASE_REF' \
     RELEASE_COMMIT='$RELEASE_COMMIT' \
     SERVICE_NAME='$SERVICE_NAME' \
     bash -s" <<'REMOTE'
set -Eeuo pipefail

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_PATH="${REMOTE_APP}.backup-${TIMESTAMP}"
FAILED_PATH="${REMOTE_APP}.failed-${TIMESTAMP}"
DEPLOYMENT_STARTED=0
ROLLED_BACK=0

rollback() {
    local rc=$?

    if [[ "$DEPLOYMENT_STARTED" -eq 1 && "$ROLLED_BACK" -eq 0 ]]; then
        ROLLED_BACK=1

        echo
        echo "FAIL: deployment failed; beginning rollback"

        sudo systemctl stop "$SERVICE_NAME" || true

        if [[ -d "$REMOTE_APP" ]]; then
            mv "$REMOTE_APP" "$FAILED_PATH" || true
        fi

        if [[ -d "$BACKUP_PATH" ]]; then
            mv "$BACKUP_PATH" "$REMOTE_APP"
            sudo systemctl start "$SERVICE_NAME" || true
            sleep 5
            echo "PASS: previous version restored"
        else
            echo "FAIL: backup directory unavailable"
        fi
    fi

    exit "$rc"
}

trap rollback ERR

echo
echo "--- Verifying production layout ---"

test -d "$REMOTE_APP"
test -d "$ANALOG_ROOT"

systemctl cat "$SERVICE_NAME" |
    grep -F "WorkingDirectory=$REMOTE_APP" >/dev/null

systemctl cat "$SERVICE_NAME" |
    grep -F "Environment=PYTHONPATH=$REMOTE_APP/src" >/dev/null

systemctl cat "$SERVICE_NAME" |
    grep -F "Environment=PI_SCANNER_ANALOG_ROOT=$ANALOG_ROOT" >/dev/null

echo "PASS: service paths match expected layout"

echo
echo "--- Preparing staged release ---"

rm -rf "$REMOTE_STAGE"
mkdir -p "$REMOTE_STAGE"

tar -xf "$REMOTE_ARCHIVE" -C "$REMOTE_STAGE"

chmod +x \
  "$REMOTE_STAGE/tools/p25_scalable_multi_rx_wrapper.py" \
  "$REMOTE_STAGE/tools/p25_multi_rx_sticky_launcher.py" \
  "$REMOTE_STAGE/tools/p25_rotating_log_exec.py"

test -x "$REMOTE_STAGE/tools/p25_scalable_multi_rx_wrapper.py"
test -x "$REMOTE_STAGE/tools/p25_multi_rx_sticky_launcher.py"
test -x "$REMOTE_STAGE/tools/p25_rotating_log_exec.py"

test -f "$REMOTE_STAGE/src/pi_p25_scanner/backend.py"

cd "$REMOTE_STAGE"
PYTHONPATH=src python3 -m compileall -q src/pi_p25_scanner

PYTHONPATH=src python3 - <<'PY'
from pi_p25_scanner import backend
from pi_p25_scanner import radioreference_import
from pi_p25_scanner import receiver_inventory
from pi_p25_scanner import runtime_activity

print("PASS: staged imports")
PY

echo
echo "--- Preserving P25 runtime data ---"

for item in \
    runtime \
    settings \
    config \
    configs \
    data \
    logs \
    .env
do
    if [[ -e "$REMOTE_APP/$item" ]]; then
        rm -rf "$REMOTE_STAGE/$item"
        cp -a "$REMOTE_APP/$item" "$REMOTE_STAGE/$item"
        echo "PRESERVED=$item"
    fi
done

cat > "$REMOTE_STAGE/DEPLOYED_RELEASE.txt" <<EOF
release_ref=$RELEASE_REF
release_commit=$RELEASE_COMMIT
deployed_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
previous_path=$BACKUP_PATH
analog_root=$ANALOG_ROOT
EOF

echo
echo "--- Stopping service ---"

sudo systemctl stop "$SERVICE_NAME"
DEPLOYMENT_STARTED=1

if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "FAIL: service did not stop"
    exit 1
fi

echo "PASS: service stopped"

echo
echo "--- Installing release ---"

mv "$REMOTE_APP" "$BACKUP_PATH"
mv "$REMOTE_STAGE" "$REMOTE_APP"

echo "BACKUP_PATH=$BACKUP_PATH"

sudo systemctl daemon-reload
sudo systemctl start "$SERVICE_NAME"

sleep 8

if ! sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "FAIL: service failed to start"
    sudo systemctl --no-pager --full status "$SERVICE_NAME" || true
    exit 1
fi

echo "PASS: service active"

echo
echo "--- API checks ---"

status_json="$(curl -fsS --max-time 10 http://127.0.0.1:8070/api/status)"
activity_json="$(curl -fsS --max-time 10 http://127.0.0.1:8070/api/activity)"
analog_json="$(curl -fsS --max-time 10 http://127.0.0.1:8070/api/analog/status)"

python3 - "$status_json" "$activity_json" "$analog_json" <<'PY'
import json
import sys

names = (
    "/api/status",
    "/api/activity",
    "/api/analog/status",
)

for name, raw in zip(names, sys.argv[1:]):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SystemExit(f"FAIL: {name} did not return an object")
    print(f"PASS: {name}")
PY

echo
echo "--- Confirming analog root untouched ---"

test -d "$ANALOG_ROOT"
echo "PASS: analog root remains at $ANALOG_ROOT"

echo
echo "--- Service status ---"

sudo systemctl --no-pager --full status "$SERVICE_NAME"

trap - ERR

echo
echo "FINAL=PASS"
echo "RELEASE_REF=$RELEASE_REF"
echo "RELEASE_COMMIT=$RELEASE_COMMIT"
echo "BACKUP_PATH=$BACKUP_PATH"
echo "P25_APP=$REMOTE_APP"
echo "ANALOG_ROOT=$ANALOG_ROOT"
REMOTE

echo
echo "PASS: release candidate deployment completed"
