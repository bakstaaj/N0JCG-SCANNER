#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
STATUS_PATH="${STATUS_PATH:-/tmp/pi-scanner-uhf-smoke-status.json}"
BRIDGE_HTTP_PORT="${BRIDGE_HTTP_PORT:-18074}"
BRIDGE_UDP_PORT="${BRIDGE_UDP_PORT:-24459}"
BRIDGE_LOG="${BRIDGE_LOG:-/tmp/pi-scanner-uhf-bridge-smoke.log}"
BRIDGE_PID=""

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

pass() { printf 'PASS: %s\n' "$*"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { printf 'WARN: %s\n' "$*"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { printf 'FAIL: %s\n' "$*" >&2; FAIL_COUNT=$((FAIL_COUNT + 1)); }

cleanup() {
  if [[ -n "$BRIDGE_PID" ]]; then
    kill "$BRIDGE_PID" >/dev/null 2>&1 || true
    wait "$BRIDGE_PID" >/dev/null 2>&1 || true
  fi
}
finish() {
  cleanup
  printf '\nSUMMARY: PASS=%d WARN=%d FAIL=%d\n' \
    "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT"
  if [[ "$FAIL_COUNT" -eq 0 ]]; then
    printf 'FINAL: PASS\n'
  else
    printf 'FINAL: FAIL\n'
  fi
}
trap finish EXIT

printf '=== PI-SCANNER isolated UHF phase smoke ===\n'

for command_name in python3 rtl_tcp timeout; do
  if command -v "$command_name" >/dev/null 2>&1; then
    pass "command available: $command_name"
  else
    fail "missing command: $command_name"
  fi
done
[[ "$FAIL_COUNT" -eq 0 ]] || exit 1

cd "$ROOT"
export PYTHONPATH="$ROOT/src"

python3 -m pi_p25_scanner.analog_uhf_worker --self-test
pass "UHF worker self-test passed"

python3 tools/pi_scanner_uhf_audio_bridge.py --self-test
pass "UHF audio bridge self-test passed"

rm -f "$BRIDGE_LOG"
python3 tools/pi_scanner_uhf_audio_bridge.py \
  --host 127.0.0.1 \
  --port "$BRIDGE_HTTP_PORT" \
  --udp-host 127.0.0.1 \
  --udp-port "$BRIDGE_UDP_PORT" \
  >"$BRIDGE_LOG" 2>&1 &
BRIDGE_PID="$!"
sleep 0.7

python3 - "$BRIDGE_HTTP_PORT" "$BRIDGE_UDP_PORT" <<'PY'
import json
import socket
import struct
import sys
import time
import urllib.request

http_port = int(sys.argv[1])
udp_port = int(sys.argv[2])
frame = struct.pack("<160h", *([2500] * 160))

sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for _ in range(5):
    sender.sendto(frame, ("127.0.0.1", udp_port))
    time.sleep(0.02)
sender.close()

deadline = time.time() + 3.0
last = None
while time.time() < deadline:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{http_port}/api/audio/status",
            timeout=1.0,
        ) as response:
            last = json.loads(response.read().decode("utf-8"))
        if int(last.get("accepted_frames") or 0) >= 5:
            break
    except Exception:
        pass
    time.sleep(0.1)

if not last or int(last.get("accepted_frames") or 0) < 5:
    raise SystemExit(f"bridge network smoke failed: {last!r}")
print("UHF_BRIDGE_NETWORK_SMOKE=PASS")
PY
pass "UHF bridge UDP-to-HTTP status path passed"

cleanup
BRIDGE_PID=""

if pgrep -af 'rtl_(fm|tcp)' 2>/dev/null | grep -F -- "00000440" >/dev/null; then
  fail "UHF RTL serial 00000440 is already in use"
  exit 1
fi
pass "UHF RTL serial 00000440 is not already in use"

rm -f "$STATUS_PATH"
timeout 20 python3 -m pi_p25_scanner.analog_uhf_worker \
  --status-path "$STATUS_PATH" \
  --smoke-seconds 8 \
  --no-forward
pass "UHF RTL hardware PCM smoke command completed"

python3 - "$STATUS_PATH" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    raise SystemExit(f"status file missing: {path}")
status = json.loads(path.read_text(encoding="utf-8"))
checks = {
    "state": status.get("state") == "smoke_passed",
    "serial": status.get("receiver_serial") == "00000440",
    "channels": int(status.get("configured_channel_count") or 0) > 0,
    "fft_sweeps": int(status.get("spectrum_sweeps") or 0) > 0,
    "mode": status.get("search_mode") == "fft_directed_nfm_v2",
    "forwarding_disabled": status.get("no_forward") is True,
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit(
        f"UHF hardware smoke status failed {failed}: {status}"
    )
print("UHF_RTL_HARDWARE_SMOKE=PASS")
PY
pass "UHF serial, uploaded channels, and FFT sweep validated"

printf '\nStatus file: %s\n' "$STATUS_PATH"
printf 'This smoke test does not start or modify P25 services.\n'
