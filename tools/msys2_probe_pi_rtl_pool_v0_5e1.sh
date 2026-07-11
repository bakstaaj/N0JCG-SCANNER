#!/usr/bin/env bash
set -Eeuo pipefail
if [[ -f .env ]]; then set -a; source .env; set +a; fi
PI_HOST="192.168.254.63"
PI_USER="${PI_USER:-pi}"
PI_ROOT="/home/pi/PI-P25-SCANNER"
SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=12)
if [[ -n "${PI_PASSWORD:-}" ]]; then export SSHPASS="$PI_PASSWORD"; SSH=(sshpass -e "${SSH_BASE[@]}");
elif [[ -n "${SSHPASS:-}" ]]; then SSH=(sshpass -e "${SSH_BASE[@]}");
else SSH=("${SSH_BASE[@]}" -o BatchMode=yes); fi
curl -fsS "http://${PI_HOST}:8070/api/status" | python3 -m json.tool
"${SSH[@]}" "${PI_USER}@${PI_HOST}" "cd '$PI_ROOT' && echo '--- runtime config serials ---' && python3 - <<'PY'
import json
from pathlib import Path
p=Path('runtime/settings/p25_systems.json')
data=json.loads(p.read_text())
for system in data.get('systems',[]):
    print(system.get('name'), system.get('receiver_roles'))
print('--- marker args ---')
for line in Path('runtime/settings/op25_validated_rx_command.env').read_text().splitlines():
    if line.startswith('P25_VALIDATED_RX_ARGS='):
        print(line)
PY"
