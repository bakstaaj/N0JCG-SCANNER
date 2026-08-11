#!/usr/bin/env bash
set -euo pipefail

export PATH=/ucrt64/bin:/usr/bin:/bin
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[[ -f .env ]] || { echo "ERROR: missing .env" >&2; exit 1; }
set -a
# shellcheck disable=SC1091
source .env
set +a

: "${PI_USER:?missing PI_USER}"
: "${PI_HOST:?missing PI_HOST}"
: "${PI_PASSWORD:?missing PI_PASSWORD}"
PI_REPO="${PI_REPO:-/home/pi/n0jcg-scanner}"

files=(
  src/pi_p25_scanner/backend.py
  src/pi_p25_scanner/runtime_activity.py
  web/app.js
  web/index.html
)

for path in "${files[@]}"; do
  [[ -f "$path" ]] || { echo "ERROR: missing $path" >&2; exit 1; }
done

python -m py_compile \
  src/pi_p25_scanner/backend.py \
  src/pi_p25_scanner/runtime_activity.py
node --check web/app.js

archive="/tmp/pi_scanner_voice_call_counter_v2_0_0_$$.tgz"
tar -czf "$archive" "${files[@]}"

ssh=(sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=accept-new)
scp=(sshpass -p "$PI_PASSWORD" scp -o StrictHostKeyChecking=accept-new)
target="${PI_USER}@${PI_HOST}"
remote_archive="/tmp/pi_scanner_voice_call_counter_v2_0_0.tgz"

"${scp[@]}" "$archive" "$target:$remote_archive"
"${ssh[@]}" "$target" bash -s -- "$PI_REPO" "$remote_archive" "${files[@]}" <<'REMOTE'
set -euo pipefail
repo="$1"
archive="$2"
shift 2
cd "$repo"
was_running=false
predeploy_calls="$(
  curl -fsS http://127.0.0.1:8070/api/status \
    | python3 -c 'import json,sys; print(int(json.load(sys.stdin).get("activity_summary", {}).get("distinct_voice_calls", 0)))'
)"
if curl -fsS http://127.0.0.1:8070/api/status \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if d.get("decoder_process", {}).get("running") else 1)'; then
  was_running=true
fi
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="runtime/patch_backups/voice_call_counter_v2_0_0_${stamp}"
mkdir -p "$backup"
for path in "$@"; do
  if [[ -f "$path" ]]; then
    mkdir -p "$backup/$(dirname "$path")"
    cp -p "$path" "$backup/$path"
  fi
done
tar -xzf "$archive"
python3 -m py_compile \
  src/pi_p25_scanner/backend.py \
  src/pi_p25_scanner/runtime_activity.py
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
fi
PYTHONPATH="$repo/src" python3 -c 'from pi_p25_scanner.runtime_activity import RuntimeActivityTracker; from pi_p25_scanner.runtime_status import RuntimeStatusUpdate; t=RuntimeActivityTracker(); u=RuntimeStatusUpdate(line="voice update", tgid=4540, voice_frequency_hz=853300000, voice_call=True); t.record(u); t.record(u); s=t.snapshot(); assert s["distinct_voice_calls"] == 1 and s["voice_call_events"] == 2'
activity_state="$repo/runtime/settings/runtime_activity.json"
python3 - "$activity_state" "$predeploy_calls" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
live_calls = max(0, int(sys.argv[2]))
try:
    saved_calls = max(0, int(json.loads(path.read_text(encoding="utf-8")).get("distinct_voice_calls", 0)))
except (OSError, ValueError, TypeError, json.JSONDecodeError):
    saved_calls = 0
payload = {
    "distinct_voice_calls": max(live_calls, saved_calls),
    "updated_utc": __import__("time").time(),
}
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, path)
print(f"PRESERVED_VOICE_CALLS={payload['distinct_voice_calls']}")
PY
sudo systemctl restart pi-p25-scanner.service
for attempt in $(seq 1 20); do
  if curl -fs http://127.0.0.1:8070/api/status >/dev/null; then
    break
  fi
  sleep 1
done
if [[ "$was_running" == true ]]; then
  curl -fsS -X POST http://127.0.0.1:8070/api/scanner/start >/dev/null
  for attempt in $(seq 1 30); do
    status="$(curl -fsS http://127.0.0.1:8070/api/status)"
    if python3 -c 'import json,sys; d=json.load(sys.stdin); a=d.get("activity_summary", {}); raise SystemExit(0 if d.get("decoder_process", {}).get("running") and "distinct_voice_calls" in a else 1)' <<<"$status"; then
      break
    fi
    sleep 1
  done
  python3 -c 'import json,sys; d=json.load(sys.stdin); activity=d["activity_summary"]; assert d["decoder_process"]["running"]; assert "distinct_voice_calls" in activity; print("DISTINCT_VOICE_CALLS=%s" % activity["distinct_voice_calls"])' <<<"$status"
else
  echo "DISTINCT_VOICE_CALLS=ready_when_scanner_starts"
fi
grep -Fq 'activity?.distinct_voice_calls ?? activity?.voice_call_events ?? 0' web/app.js
grep -Fq '/app.js?v=2.0.3-audio-arbitrator-status-2.1.0-csv-radio-profiles-2.0.0-voice-call-counter' web/index.html
echo "BACKUP=$repo/$backup"
echo "DEPLOYED=voice-call-counter-v2.0.0"
REMOTE

rm -f "$archive"
echo "PASS: voice-call counter deployed to $target:$PI_REPO"
