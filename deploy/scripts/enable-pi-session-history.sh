#!/bin/bash
set -eu
python3 - <<'PY'
import json, subprocess
k = ['kubectl', '--kubeconfig', '/home/uav/.kube/config', '-n', 'uav-mvp']
cm = json.loads(subprocess.check_output(k + ['get', 'configmap', 'uav-edge-control-config', '-o', 'json']))
config = json.loads(cm['data']['pi-runner.json'])
config['command'] = [arg for arg in config['command'] if arg != '--no-session']
patch = {'data': {'pi-runner.json': json.dumps(config, indent=2)}}
subprocess.run(k + ['patch', 'configmap', 'uav-edge-control-config', '--type=merge', '-p', json.dumps(patch)], check=True)
print('Pi session history enabled; HOME:', config['environment']['HOME'])
PY
kubectl --kubeconfig /home/uav/.kube/config -n uav-mvp rollout restart deployment/pi-runner
kubectl --kubeconfig /home/uav/.kube/config -n uav-mvp rollout status deployment/pi-runner --timeout=90s
kubectl --kubeconfig /home/uav/.kube/config -n uav-mvp exec deployment/pi-runner -- python -c 'import json; c=json.load(open("/opt/uav/config/pi-runner.json")); assert "--no-session" not in c["command"]; print("Session persistence enabled")'
