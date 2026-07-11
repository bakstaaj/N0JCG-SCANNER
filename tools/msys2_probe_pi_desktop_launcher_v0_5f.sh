#!/usr/bin/env bash
set -u
if [[ -f .env ]]; then set -a; source .env; set +a; fi
PI_HOST="192.168.254.63"
PI_USER="${PI_USER:-pi}"
PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
TARGET="$PI_USER@$PI_HOST"
SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8)
if [[ -n "${PI_PASSWORD:-}" ]]; then export SSHPASS="$PI_PASSWORD"; fi
if [[ -n "${SSHPASS:-}" ]]; then SSH_CMD=(sshpass -e "${SSH_BASE[@]}"); else SSH_CMD=("${SSH_BASE[@]}" -o BatchMode=yes); fi
"${SSH_CMD[@]}" "$TARGET" "printf '%s\n' '--- UI marker ---'; curl -fsS http://127.0.0.1:8070/app.js | grep -n 'V0.5F_DESKTOP_LAUNCHER_NO_PAGE_AUTOSTART' || true; printf '%s\n' '--- index script ---'; curl -fsS http://127.0.0.1:8070/index.html | grep -n 'app.js'; printf '%s\n' '--- desktop file ---'; ls -l /home/pi/Desktop/P25-Scanner.desktop; printf '%s\n' '--- starter ---'; ls -l '$PI_REPO/tools/start_p25_scanner_desktop.sh'; printf '%s\n' '--- status ---'; curl -fsS http://127.0.0.1:8070/api/status | python3 -m json.tool | sed -n '1,80p'"
