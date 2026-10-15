#!/bin/bash
set -eu
python3 - <<'PY'
import json,os,pathlib,shutil
import yaml
base=pathlib.Path('/etc/uav-mvp/registry')
base.mkdir(mode=0o700,parents=True,exist_ok=True)
source=pathlib.Path('/home/uav/uav-agent-mvp/tunnel')
robot=json.loads((source/'harbor-pull.json').read_text())
path=pathlib.Path('/etc/rancher/k3s/registries.yaml')
config=yaml.safe_load(path.read_text()) if path.exists() else {}
config=config or {}
mirrors=config.setdefault('mirrors',{})
settings=config.setdefault('configs',{})
entry={'endpoint':['http://47.109.49.98:35443']}
if '47.109.49.98:30881' in mirrors and mirrors['47.109.49.98:30881']!=entry:
    raise SystemExit('Existing registry mirror differs; refusing replacement')
mirrors['47.109.49.98:30881']=entry
new={'auth':{'username':robot['name'],'password':robot['secret']}}
if '47.109.49.98:35443' in settings and settings['47.109.49.98:35443']!=new:
    raise SystemExit('Existing registry credentials differ; inspect before replacement')
settings['47.109.49.98:35443']=new
if path.exists() and not path.with_suffix('.yaml.pre-uav-tunnel').exists():
    shutil.copy2(str(path),str(path.with_suffix('.yaml.pre-uav-tunnel')))
temporary=path.with_suffix('.yaml.uav-new')
with os.fdopen(os.open(str(temporary),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as out:
    json.dump(config,out,indent=2)
os.replace(str(temporary),str(path))
# 固定配置目录已存在；只加入独立片段，不覆盖此前 Jetson 内核兼容配置。
dropin=pathlib.Path('/etc/rancher/k3s/config.yaml.d')
dropin.mkdir(mode=0o700,exist_ok=True)
setting=dropin/'30-uav-registry.yaml'
content='disable-default-registry-endpoint: true\n'
if setting.exists() and setting.read_text()!=content:
    raise SystemExit('Existing registry fallback setting differs')
setting.write_text(content)
os.chmod(str(setting),0o600)
print('Private HTTP mirror configured with scoped pull credentials')
PY
systemctl restart k3s
k3s kubectl wait --for=condition=Ready node/jetson-uav-01 --timeout=120s
k3s kubectl get deployments -n uav-mvp
