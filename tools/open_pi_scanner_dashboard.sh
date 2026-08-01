#!/usr/bin/env bash
set -Eeuo pipefail

URL="${PI_SCANNER_URL:-http://127.0.0.1:8070/}"

if command -v chromium-browser >/dev/null 2>&1; then
  nohup chromium-browser --new-window "$URL" >/dev/null 2>&1 &
elif command -v chromium >/dev/null 2>&1; then
  nohup chromium --new-window "$URL" >/dev/null 2>&1 &
elif command -v x-www-browser >/dev/null 2>&1; then
  nohup x-www-browser "$URL" >/dev/null 2>&1 &
elif command -v xdg-open >/dev/null 2>&1; then
  nohup xdg-open "$URL" >/dev/null 2>&1 &
else
  echo "No browser launcher found. Open $URL manually." >&2
  exit 1
fi
