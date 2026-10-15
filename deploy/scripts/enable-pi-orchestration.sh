#!/bin/bash
set -eu
k3s kubectl apply -f - <<'YAML'
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: uav-agent-orchestration
  namespace: uav-mvp
rules:
- apiGroups: [""]
  resources: [pods, services, configmaps, persistentvolumeclaims]
  verbs: [get, list, watch, create, update, patch, delete]
- apiGroups: [apps]
  resources: [deployments, deployments/scale, replicasets, statefulsets, statefulsets/scale, daemonsets]
  verbs: [get, list, watch, create, update, patch, delete]
- apiGroups: [batch]
  resources: [jobs, cronjobs]
  verbs: [get, list, watch, create, update, patch, delete]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: uav-agent-orchestration
  namespace: uav-mvp
subjects:
- kind: ServiceAccount
  name: uav-agent
  namespace: uav-mvp
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: uav-agent-orchestration
YAML
python3 - <<'PY'
import json,pathlib,shutil,time
p=pathlib.Path('/home/uav/uav-agent-mvp/project/.mcp.json')
data=json.loads(p.read_text())
shutil.copy2(str(p),str(p)+'.before-write-'+str(int(time.time())))
args=data['mcpServers']['kubernetes']['args']
data['mcpServers']['kubernetes']['args']=[x for x in args if x not in ('--read-only','--disable-destructive')]
p.write_text(json.dumps(data,indent=2)+'\n')
instructions=p.parent/'AGENTS.md'
text=instructions.read_text()
text=text.replace('当前是只读查询环境，写操作任务如实报告尚未接通。','当前已开放 uav-mvp 内基本编排。使用 resources_create_or_update 创建/更新完整资源，resources_scale 扩缩容，resources_delete 删除用户指定资源。用户要求部署时实际执行并查询就绪结果，保护与任务无关的现有服务。')
instructions.write_text(text)
PY
for resource in deployments.apps services configmaps jobs.batch; do
  k3s kubectl auth can-i create "$resource" -n uav-mvp --as=system:serviceaccount:uav-mvp:uav-agent
done
