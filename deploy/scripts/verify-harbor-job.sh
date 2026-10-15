#!/bin/bash
set -eu
python3 - <<'PY'
import base64,json,pathlib,subprocess
root=pathlib.Path('/home/uav/uav-agent-mvp')
robot=json.loads((root/'tunnel/harbor-pull.json').read_text())
auth={'auths':{'47.109.49.98:30881':{'username':robot['name'],'password':robot['secret']}}}
secret={'apiVersion':'v1','kind':'Secret','metadata':{'name':'uav-harbor-pull','namespace':'uav-mvp'},'type':'kubernetes.io/dockerconfigjson','data':{'.dockerconfigjson':base64.b64encode(json.dumps(auth).encode()).decode()}}
subprocess.run(['k3s','kubectl','apply','-f','-'],input=json.dumps(secret).encode(),check=True,stdout=subprocess.DEVNULL)
image='47.109.49.98:30881/uav-mvp/crypto-service@sha256:992bb941ccae1f8d81c6399d6d4eca738c809f4eeec0dc1e45b136d73efbb238'
job={'apiVersion':'batch/v1','kind':'Job','metadata':{'name':'harbor-pull-verification','namespace':'uav-mvp'},'spec':{'backoffLimit':0,'activeDeadlineSeconds':180,'template':{'spec':{'restartPolicy':'Never','automountServiceAccountToken':False,'imagePullSecrets':[{'name':'uav-harbor-pull'}],'containers':[{'name':'verify','image':image,'imagePullPolicy':'Always','command':['python','-c','import platform; from cryptography.hazmat.primitives.ciphers import algorithms; print(platform.machine(), algorithms.SM4.name)'],'securityContext':{'runAsUser':1000,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},'resources':{'requests':{'cpu':'50m','memory':'64Mi'},'limits':{'cpu':'1','memory':'128Mi'}}}]}}}}
subprocess.run(['k3s','kubectl','create','-f','-'],input=json.dumps(job).encode(),check=True)
PY
k3s kubectl wait -n uav-mvp --for=condition=complete job/harbor-pull-verification --timeout=150s || true
k3s kubectl get pods -n uav-mvp -l job-name=harbor-pull-verification
k3s kubectl logs -n uav-mvp job/harbor-pull-verification --pod-running-timeout=10s
