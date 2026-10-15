"""只读复核前端提交的六组合任务及当前云边状态，不提交新任务。"""
import argparse
import json
from pathlib import Path

from deploy.scripts.remote_control import connect, run, kubectl


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task-ids', nargs='+', required=True)
    parser.add_argument('--expected-policy', choices=['Ed25519', 'RSA', 'SM2'], required=True)
    parser.add_argument('--expected-suite', choices=['AES-256-GCM', 'SM4-GCM'], required=True)
    parser.add_argument('--auth-only-task-id', help='复核最后一个只切换认证的前端任务')
    args = parser.parse_args()
    cloud, edge = connect('47.109.49.98', 24531), connect('192.168.8.101')
    try:
        script = '''import json,ssl,urllib.request,time
from pathlib import Path
root=Path('/home/uav/uav-agent-mvp')
token=(root/'control/keys/operator.token').read_text().strip()
context=ssl.create_default_context(cafile=str(root/'tls/ca.crt'))
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
def request(path):
 req=urllib.request.Request('https://localhost:8443'+path,headers={'Authorization':'Bearer '+token})
 with opener.open(req,timeout=15) as response: return json.load(response)
results=[]
for identifier in TASK_IDS:
 task=request('/api/tasks/'+identifier)
 evidence=task.get('result') or {}
 assert task['state']=='succeeded', identifier
 assert all(evidence.get(name) is True for name in ('identity_verified','bidirectional_probe_verified','cloud_target_ready','new_session_business_verified')), identifier
 results.append({'task_id':identifier,'scenario':task['scenario'],'state':task['state'],'evidence':evidence})
overview=request('/api/overview')
identity,communication=overview['identity_auth'],overview['communication']
assert identity['authenticated'] and identity['bidirectional_confirmation'] and identity['expires_at']>time.time()
assert identity['policy']==EXPECTED_POLICY and identity['suite']==EXPECTED_SUITE
assert identity['epoch']==communication['epoch'] and identity['suite']==communication['suite'] and identity['policy']==communication['auth_policy']
assert overview['device']['online']
telemetry=(overview['device'].get('status') or {}).get('telemetry') or {}
assert telemetry.get('online') and telemetry.get('read_only') and telemetry.get('invalid_frames')==0
print(json.dumps({'tasks':results,'identity_auth':identity,'communication':communication,'telemetry_online':True,'read_only':True,'invalid_frames':0}))
'''
        script = script.replace('TASK_IDS', repr(args.task_ids)).replace('EXPECTED_POLICY', repr(args.expected_policy)).replace('EXPECTED_SUITE', repr(args.expected_suite))
        record = json.loads(run(cloud, 'python3 -', data=script, timeout=90))
        combinations = {(item['evidence']['auth_policy'], item['evidence']['suite']) for item in record['tasks']}
        expected = {(policy, suite) for policy in ('Ed25519', 'RSA', 'SM2') for suite in ('AES-256-GCM', 'SM4-GCM')}
        if combinations != expected:
            raise ValueError('frontend task evidence does not cover all six combinations')
        if args.auth_only_task_id:
            final = record['tasks'][-1]
            previous = record['tasks'][-2]
            if (final['task_id'] != args.auth_only_task_id or final['evidence']['suite'] != previous['evidence']['suite'] or
                    final['evidence']['epoch'] == previous['evidence']['epoch'] or
                    final['evidence']['auth_policy'] == previous['evidence']['auth_policy']):
                raise ValueError('authentication-only switch did not preserve suite and establish a new epoch')
            record['authentication_only_switch_verified'] = True
        deployments = json.loads(kubectl(edge, 'get deploy -o json'))
        required = {'pi-runner', 'uav-edge-agent'}
        required.add('sm4-service' if args.expected_suite == 'SM4-GCM' else 'aes-service')
        ready = {item['metadata']['name']: item.get('status', {}).get('readyReplicas', 0) for item in deployments['items'] if item['metadata']['name'] in required}
        if set(ready) != required or any(value != 1 for value in ready.values()):
            raise ValueError('required Jetson deployments are not ready')
        record['jetson_deployments_ready'] = ready
        auth_names = {'auth-' + policy.lower() for policy in ('Ed25519', 'RSA', 'SM2')}
        auth_replicas = {item['metadata']['name']: {'desired': item['spec']['replicas'],
                        'ready': item.get('status', {}).get('readyReplicas', 0)}
                        for item in deployments['items'] if item['metadata']['name'] in auth_names}
        expected_auth = {'auth-' + policy.lower(): {'desired': int(policy == args.expected_policy),
                        'ready': int(policy == args.expected_policy)} for policy in ('Ed25519', 'RSA', 'SM2')}
        if auth_replicas != expected_auth:
            raise ValueError('Jetson authentication deployments do not match the active policy')
        record['jetson_authentication_replicas'] = auth_replicas
        nodes = json.loads(kubectl(edge, 'get nodes -o json'))
        record['jetson_node_count'] = len(nodes['items'])
        if record['jetson_node_count'] != 1:
            raise ValueError('unexpected Jetson node count')
        record['verified_at'] = int(__import__('time').time())
        destination = Path(__file__).resolve().parents[2] / 'docs/evidence/deploy/auth-combination-live-verification.json'
        destination.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(record, ensure_ascii=False))
    finally:
        edge.close()
        cloud.close()


if __name__ == '__main__':
    main()
