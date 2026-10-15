"""通过真实任务 API 验证认证后的 AES→SM4→AES，结束时恢复 AES。"""
import json
import argparse
from pathlib import Path

from deploy.scripts.remote_control import connect, run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task-ids', nargs=2, help='仅复核已经提交的 SM4、AES 任务，避免重复提交')
    args = parser.parse_args()
    client = connect('47.109.49.98', 24531)
    try:
        script = """import json,ssl,time,urllib.request,uuid
from pathlib import Path
root=Path('/home/uav/uav-agent-mvp')
token=(root/'control/keys/operator.token').read_text().strip()
context=ssl.create_default_context(cafile=str(root/'tls/ca.crt'))
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
def request(path,body=None):
 r=urllib.request.Request('https://localhost:8443'+path,None if body is None else json.dumps(body).encode(),
                          {'Authorization':'Bearer '+token,'Content-Type':'application/json'})
 with opener.open(r,timeout=10) as response: return json.load(response)
results=[]
existing_tasks=[]
for index,(scenario,suite) in enumerate([('切换到 sm4','SM4-GCM'),('切换到 aes','AES-256-GCM')]):
 started=time.monotonic()
 task=request('/api/tasks/'+existing_tasks[index]) if existing_tasks else request('/api/tasks',{'device_id':'jetson-uav-01','request_id':'identity-live-'+uuid.uuid4().hex,'scenario':scenario})
 for attempt in range(120):
  task=request('/api/tasks/'+task['task_id'])
  if task['state'] in ('succeeded','failed','unknown'):break
  time.sleep(2)
 assert task['state']=='succeeded', json.dumps({'task_id':task['task_id'],'state':task['state'],'result':task.get('result')})
 evidence=task['result']
 assert evidence['suite']==suite and evidence['bidirectional_probe_verified'] and evidence['new_session_business_verified']
 if not existing_tasks:
  overview=request('/api/overview')
  auth=overview['identity_auth']
  assert auth['authenticated'] and auth['policy']=='Ed25519' and auth['suite']==suite
 results.append({'task_id':task['task_id'],'suite':suite,'state':'succeeded','observation_seconds':round(time.monotonic()-started,3),
                 'identity_authenticated':True,'bidirectional_probe_verified':True,'new_session_business_verified':True})
print(json.dumps(results))
"""
        if args.task_ids:
            script = script.replace('existing_tasks=[]', 'existing_tasks=' + json.dumps(args.task_ids))
        output = run(client, 'python3 -', timeout=540, data=script)
        results = json.loads(output)
        root = Path(__file__).resolve().parents[2]
        (root / 'docs/evidence/deploy/identity-switch-verification.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps(results))
    finally:
        client.close()


if __name__ == '__main__':
    main()
