#!/bin/bash
set -eu
export KUBECONFIG=/home/uav/.kube/config
test -r "$KUBECONFIG"
k3s kubectl --request-timeout=10s auth can-i '*' '*' | grep -qx yes
python3 - <<'PY'
import base64
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import yaml

root = Path('/home/uav/uav-agent-mvp')
stamp = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
envfile = root / 'env.sh'
text = envfile.read_text()
line = 'export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"'
if line not in text:
    shutil.copy2(str(envfile), str(envfile) + '.before-k3s-access-' + stamp)
    envfile.write_text(text.rstrip() + '\n' + line + '\n')

path = root / 'mcp/kubeconfig'
document = yaml.safe_load(path.read_text())
context = next(item['context'] for item in document['contexts'] if item['name'] == document['current-context'])
user = next(item['user'] for item in document['users'] if item['name'] == context['user'])
part = user['token'].split('.')[1]
claims = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
if claims.get('sub') != 'system:serviceaccount:uav-mvp:uav-agent':
    raise RuntimeError('Unexpected restricted credential identity')
command = ['k3s', 'kubectl', '--request-timeout=10s', '-n', 'uav-mvp']
secret_name = 'uav-agent-mcp-test-token'
raw = subprocess.check_output(command + ['get', 'secret', secret_name, '--ignore-not-found', '-o', 'json'])
if not raw.strip():
    manifest = {'apiVersion': 'v1', 'kind': 'Secret',
                'metadata': {'name': secret_name, 'namespace': 'uav-mvp',
                             'annotations': {'kubernetes.io/service-account.name': 'uav-agent'}},
                'type': 'kubernetes.io/service-account-token'}
    subprocess.run(command + ['create', '-f', '-'], input=json.dumps(manifest).encode(), check=True)
for attempt in range(20):
    secret = json.loads(subprocess.check_output(command + ['get', 'secret', secret_name, '-o', 'json']))
    if (secret.get('type') != 'kubernetes.io/service-account-token' or
            secret['metadata'].get('annotations', {}).get('kubernetes.io/service-account.name') != 'uav-agent'):
        raise RuntimeError('Unexpected token Secret identity')
    encoded = secret.get('data', {}).get('token')
    if encoded:
        break
    time.sleep(1)
else:
    raise RuntimeError('ServiceAccount token controller did not populate Secret')
token = base64.b64decode(encoded).decode()
part = token.split('.')[1]
claims = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
if claims.get('sub') != 'system:serviceaccount:uav-mvp:uav-agent' or 'exp' in claims:
    raise RuntimeError('Expected a non-expiring token for the existing ServiceAccount')
user['token'] = token
backup = str(path) + '.before-renewal-' + stamp
shutil.copy2(str(path), backup)
os.chmod(backup, 0o600)
fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.kubeconfig-')
try:
    with os.fdopen(fd, 'w') as out:
        yaml.safe_dump(document, out)
        out.flush()
        os.fsync(out.fileno())
    subprocess.run(['k3s', 'kubectl', '--kubeconfig', temporary, '--request-timeout=10s',
                    '-n', 'uav-mvp', 'get', 'deployments'], check=True)
    os.replace(temporary, str(path))
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
part = token.split('.')[1]
claims = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
print('Restricted token expiration: none (manual ServiceAccount Secret: ' + secret_name + ')')
print('User shell kubeconfig configured; test credential installed without changing RBAC.')
PY
k3s kubectl --request-timeout=10s get nodes
k3s kubectl --kubeconfig /home/uav/uav-agent-mvp/mcp/kubeconfig --request-timeout=10s get deployments -n uav-mvp
