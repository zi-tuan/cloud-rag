#!/bin/bash
set -eu
python3 - <<'PY'
import json
import os
import subprocess

token = os.environ['UAV_ADMIN_TOKEN']
raw = subprocess.check_output([
    'curl', '--noproxy', '*', '--silent', '--show-error',
    '--resolve', 'localhost:32443:192.168.2.102',
    '--cacert', '/home/uav/uav-agent-mvp/tls/ca.crt',
    '-H', 'Authorization: Bearer ' + token,
    'https://localhost:32443/api/overview',
], timeout=15)
data = json.loads(raw)
print(json.dumps({'device': data['device'], 'tasks': len(data['tasks'])}))
PY
