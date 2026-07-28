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
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"

files=(
  src/pi_p25_scanner/analog_channels.py
  src/pi_p25_scanner/backend.py
  src/pi_p25_scanner/config_store.py
  src/pi_p25_scanner/csv_profile_tools.py
  src/pi_p25_scanner/p25_csv_import.py
  web/app.css
  web/app.js
  web/chirp_analog_template.csv
  web/index.html
  web/p25_import_template.csv
)

for path in "${files[@]}"; do
  [[ -f "$path" ]] || { echo "ERROR: missing $path" >&2; exit 1; }
done

python -m py_compile \
  src/pi_p25_scanner/analog_channels.py \
  src/pi_p25_scanner/backend.py \
  src/pi_p25_scanner/config_store.py \
  src/pi_p25_scanner/csv_profile_tools.py \
  src/pi_p25_scanner/p25_csv_import.py
node --check web/app.js

archive="/tmp/pi_scanner_csv_profiles_v2_1_0_$$.tgz"
tar -czf "$archive" "${files[@]}"

ssh=(sshpass -p "$PI_PASSWORD" ssh -o StrictHostKeyChecking=accept-new)
scp=(sshpass -p "$PI_PASSWORD" scp -o StrictHostKeyChecking=accept-new)
target="${PI_USER}@${PI_HOST}"
remote_archive="/tmp/pi_scanner_csv_profiles_v2_1_0.tgz"

"${scp[@]}" "$archive" "$target:$remote_archive"
"${ssh[@]}" "$target" bash -s -- "$PI_REPO" "$remote_archive" "${files[@]}" <<'REMOTE'
set -euo pipefail
repo="$1"
archive="$2"
shift 2
cd "$repo"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="runtime/patch_backups/csv_radio_profiles_v2_1_0_${stamp}"
mkdir -p "$backup"
for path in "$@"; do
  if [[ -f "$path" ]]; then
    mkdir -p "$backup/$(dirname "$path")"
    cp -p "$path" "$backup/$path"
  fi
done
tar -xzf "$archive"
python3 -m py_compile \
  src/pi_p25_scanner/analog_channels.py \
  src/pi_p25_scanner/backend.py \
  src/pi_p25_scanner/config_store.py \
  src/pi_p25_scanner/csv_profile_tools.py \
  src/pi_p25_scanner/p25_csv_import.py
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
fi
sudo systemctl restart pi-p25-scanner.service
for attempt in $(seq 1 20); do
  if curl -fs http://127.0.0.1:8070/api/status >/dev/null; then
    break
  fi
  sleep 1
done
curl -fsS http://127.0.0.1:8070/api/status >/dev/null
curl -fsS http://127.0.0.1:8070/api/config/named >/dev/null
curl -fsS http://127.0.0.1:8070/api/analog/channels >/dev/null
curl -fsS http://127.0.0.1:8070/chirp_analog_template.csv >/dev/null
curl -fsS http://127.0.0.1:8070/p25_import_template.csv >/dev/null
echo "BACKUP=$repo/$backup"
echo "DEPLOYED=csv-radio-profiles-v2.1.0"
REMOTE

rm -f "$archive"
echo "PASS: CSV radio profile workflow deployed to $target:$PI_REPO"
