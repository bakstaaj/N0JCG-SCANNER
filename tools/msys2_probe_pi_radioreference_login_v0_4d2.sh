#!/usr/bin/env bash
set -Eeuo pipefail
PI_USER="${PI_USER:-pi}"
PI_HOST="192.168.254.63"
SSH_BASE=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [[ -n "${PI_PASSWORD:-${SSHPASS:-}}" ]] && command -v sshpass >/dev/null 2>&1; then
  export SSHPASS="${PI_PASSWORD:-${SSHPASS:-}}"
  SSH_BASE=(sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
fi
"${SSH_BASE[@]}" "${PI_USER}@${PI_HOST}" "python3 - <<'PY'
import json, urllib.request, urllib.error
base='http://127.0.0.1:8070'
for path, method in [('/api/radioreference/status','GET'),('/api/radioreference/test-login','POST')]:
    req=urllib.request.Request(base+path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload=json.loads(response.read().decode('utf-8','replace'))
    except urllib.error.HTTPError as exc:
        payload=json.loads(exc.read().decode('utf-8','replace') or '{}')
        payload['http_status']=exc.code
    # sanitize any large or sensitive-ish user data in console output
    if isinstance(payload.get('user_data'), dict):
        payload['user_data_keys']=sorted(payload['user_data'].keys())
        payload.pop('user_data', None)
    if isinstance(payload.get('methods'), list):
        payload['method_count']=len(payload['methods'])
        payload['methods_sample']=payload['methods'][:20]
        payload.pop('methods', None)
    print(path, json.dumps(payload, indent=2, sort_keys=True))
PY"
