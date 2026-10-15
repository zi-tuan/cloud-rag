#!/bin/bash
set -eu
python3 - <<'PY'
import json, subprocess
k=['kubectl','--kubeconfig','/home/uav/.kube/config','-n','uav-mvp']
patch={'spec':{'template':{'spec':{'hostname':None,'volumes':[{'name':'pi-source','hostPath':{'path':'/home/uav/uav-agent-mvp/src/pi','type':'Directory'}}],'containers':[{'name':'pi-runner','volumeMounts':[{'name':'pi-source','mountPath':'/workspace/pi','readOnly':True}]}]}}}}
subprocess.run(k+['patch','deployment','pi-runner','--type=strategic','-p',json.dumps(patch)],check=True)
cm=json.loads(subprocess.check_output(k+['get','configmap','uav-edge-control-config','-o','json']))
c=json.loads(cm['data']['pi-runner.json'])
c['environment'].update(USER='node',LOGNAME='node')
subprocess.run(k+['patch','configmap','uav-edge-control-config','--type=merge','-p',json.dumps({'data':{'pi-runner.json':json.dumps(c,indent=2)}})],check=True)
PY
kubectl --kubeconfig /home/uav/.kube/config -n uav-mvp rollout status deployment/pi-runner --timeout=90s
