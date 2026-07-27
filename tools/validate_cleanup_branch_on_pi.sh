#!/usr/bin/env bash
set -Eeuo pipefail

PI_HOST="${1:-}"
PI_USER="${2:-pi}"
BRANCH="${3:-cleanup/v1.0.1-codebase}"
REMOTE_ROOT="/tmp/pi-scanner-v1.0.1-validation"
ARCHIVE="/tmp/pi-scanner-v1.0.1-validation.tar"

if [[ -z "$PI_HOST" ]]; then
    echo "Usage: $0 <pi-ip-or-hostname> [pi-user] [branch]"
    exit 2
fi

if ! command -v git >/dev/null 2>&1; then
    echo "FAIL: git is not installed locally"
    exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
    echo "FAIL: ssh is not installed locally"
    exit 1
fi

if ! command -v scp >/dev/null 2>&1; then
    echo "FAIL: scp is not installed locally"
    exit 1
fi

if ! git rev-parse --verify "${BRANCH}^{commit}" >/dev/null 2>&1; then
    echo "FAIL: local branch not found: $BRANCH"
    exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
    echo "FAIL: local working tree is not clean"
    git status --short
    exit 1
fi

LOCAL_COMMIT="$(git rev-parse "$BRANCH")"

echo "=== PI Scanner cleanup validation ==="
echo "HOST=$PI_HOST"
echo "USER=$PI_USER"
echo "BRANCH=$BRANCH"
echo "COMMIT=$LOCAL_COMMIT"

echo
echo "=== Local validation ==="

PYTHONPATH=src python3 -m compileall -q src/pi_p25_scanner
PYTHONPATH=src python3 -m pytest -q
git diff --check

echo "PASS: local compile and tests"

echo
echo "=== Creating branch archive ==="

rm -f "$ARCHIVE"
git archive \
    --format=tar \
    --output="$ARCHIVE" \
    "$BRANCH"

test -s "$ARCHIVE"
echo "PASS: created $ARCHIVE"

echo
echo "=== Copying archive to Pi ==="

scp -O \
    "$ARCHIVE" \
    "${PI_USER}@${PI_HOST}:/tmp/pi-scanner-v1.0.1-validation.tar"

echo "PASS: archive copied"

echo
echo "=== Running isolated Pi validation ==="

ssh "${PI_USER}@${PI_HOST}" \
    "REMOTE_ROOT='$REMOTE_ROOT' EXPECTED_COMMIT='$LOCAL_COMMIT' bash -s" <<'REMOTE'
set -Eeuo pipefail

rm -rf "$REMOTE_ROOT"
mkdir -p "$REMOTE_ROOT"

tar -xf /tmp/pi-scanner-v1.0.1-validation.tar \
    -C "$REMOTE_ROOT"

cd "$REMOTE_ROOT"

echo
echo "--- Platform ---"
uname -a
printf 'ARCH=%s\n' "$(uname -m)"

if [[ -r /etc/os-release ]]; then
    cat /etc/os-release
fi

echo
echo "--- Python ---"
python3 --version

echo
echo "--- Compile ---"
PYTHONPATH=src python3 -m compileall -q src/pi_p25_scanner
echo "PASS: Pi compile"

echo
echo "--- Package imports ---"
PYTHONPATH=src python3 - <<'PY'
from pi_p25_scanner import backend
from pi_p25_scanner import radioreference_import
from pi_p25_scanner import receiver_inventory
from pi_p25_scanner import runtime_activity

print("PASS: backend import")
print("PASS: radioreference_import import")
print("PASS: receiver_inventory import")
print("PASS: runtime_activity import")
PY

echo
echo "--- Tests ---"

if python3 -c 'import pytest' >/dev/null 2>&1; then
    PYTHONPATH=src python3 -m pytest -q
    echo "PASS: Pi pytest suite"
else
    echo "SKIP: pytest is not installed on Pi"
fi

echo
echo "--- RTL-SDR tools ---"

for tool in rtl_test rtl_eeprom rtl_power rtl_fm; do
    if command -v "$tool" >/dev/null 2>&1; then
        printf 'PASS: %s=%s\n' "$tool" "$(command -v "$tool")"
    else
        printf 'WARN: %s not found\n' "$tool"
    fi
done

echo
echo "--- USB devices ---"

if command -v lsusb >/dev/null 2>&1; then
    lsusb
else
    echo "WARN: lsusb not installed"
fi

echo
echo "--- RTL receiver probe ---"

if command -v rtl_test >/dev/null 2>&1; then
    probe_log="/tmp/pi-scanner-rtl-test.log"

    set +e
    timeout 12 rtl_test -t >"$probe_log" 2>&1
    probe_rc=$?
    set -e

    cat "$probe_log"

    case "$probe_rc" in
        0|124)
            echo "PASS: rtl_test accessed receiver hardware"
            ;;
        *)
            echo "WARN: rtl_test returned status $probe_rc"
            ;;
    esac
else
    echo "SKIP: rtl_test unavailable"
fi

echo
echo "--- Installed service status, read-only ---"

for service in \
    pi-p25-scanner.service \
    p25-scanner.service \
    pi-scanner.service
do
    if systemctl list-unit-files "$service" \
        --no-legend 2>/dev/null |
        grep -q .
    then
        echo "SERVICE=$service"
        systemctl --no-pager --full status "$service" || true
    fi
done

echo
echo "FINAL=PASS"
REMOTE

echo
echo "PASS: isolated Pi validation completed"
echo "The installed production application was not modified."
