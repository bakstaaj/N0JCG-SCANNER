#!/usr/bin/env bash
set -Eeuo pipefail
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="/c/Users/jim/Downloads/pi-p25-command-logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/probe_v0_4h3_decoder_exit_${TS}.txt"
exec > >(tee -a "$LOG_FILE") 2>&1
PI_USER="${PI_USER:-pi}"; PI_HOST="${PI_HOST:-192.168.254.63}"; PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
if [[ -f .env ]]; then set -a; source .env || true; set +a; fi
PI_USER="${PI_USER:-pi}"; PI_HOST="${PI_HOST:-192.168.254.63}"; PI_REPO="${PI_REPO:-/home/pi/PI-P25-SCANNER}"
ssh_cmd=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "${PI_USER}@${PI_HOST}")
if [[ -n "${SSHPASS:-${PI_PASSWORD:-}}" ]] && command -v sshpass >/dev/null 2>&1; then export SSHPASS="${SSHPASS:-$PI_PASSWORD}"; ssh_cmd=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "${PI_USER}@${PI_HOST}"); fi
"${ssh_cmd[@]}" bash -s -- "$PI_REPO" <<'REMOTE'
set -Eeuo pipefail
repo="$1"
cd "$repo"
echo '=== service status before start ==='
systemctl --no-pager --full status pi-p25-scanner.service || true
echo '=== status before start ==='
python3 - <<'PY' || true
import urllib.request,json
for path in ['/api/status','/api/activity']:
    try:
        with urllib.request.urlopen('http://127.0.0.1:8070'+path, timeout=3) as r:
            print(path, json.dumps(json.loads(r.read().decode('utf-8')), indent=2)[:4000])
    except Exception as e:
        print(path, 'ERROR', repr(e))
PY
echo '=== stop scanner ==='
python3 - <<'PY' || true
import urllib.request
req=urllib.request.Request('http://127.0.0.1:8070/api/scanner/stop', data=b'{}', method='POST', headers={'Content-Type':'application/json'})
try:
    print(urllib.request.urlopen(req, timeout=5).read().decode('utf-8')[:2000])
except Exception as e:
    print('stop error', repr(e))
PY
sleep 1
echo '=== start scanner ==='
python3 - <<'PY' || true
import urllib.request
req=urllib.request.Request('http://127.0.0.1:8070/api/scanner/start', data=b'{}', method='POST', headers={'Content-Type':'application/json'})
try:
    print(urllib.request.urlopen(req, timeout=8).read().decode('utf-8')[:5000])
except Exception as e:
    print('start error', repr(e))
PY
sleep 6
echo '=== status 6s after start ==='
python3 - <<'PY' || true
import urllib.request,json
try:
    with urllib.request.urlopen('http://127.0.0.1:8070/api/status', timeout=3) as r:
        data=json.loads(r.read().decode('utf-8'))
    keep={k:data.get(k) for k in ['scanner_state','last_event','active_tgid','active_talkgroup_label','last_active_tgid','last_active_talkgroup_label','updated_utc']}
    keep['decoder_process']=data.get('decoder_process')
    keep['log_tail']=data.get('log_tail', [])[-40:]
    print(json.dumps(keep, indent=2))
except Exception as e:
    print('status error', repr(e))
PY
echo '=== journal tail ==='
journalctl -u pi-p25-scanner.service --no-pager -n 160 || true
echo '=== validated command marker ==='
sed -n '1,220p' runtime/settings/op25_validated_rx_command.env 2>/dev/null || true
REMOTE
echo "UPLOAD_FILE_MSYS=$LOG_FILE"
echo "UPLOAD_FILE_WINDOWS=$(cygpath -w "$LOG_FILE" 2>/dev/null || echo "$LOG_FILE")"
