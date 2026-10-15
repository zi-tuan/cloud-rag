"""Sequential live acceptance tests; writes task responses and cluster snapshots."""
import json
import pathlib
import subprocess
import time
import urllib.request
import uuid

BASE = 'http://localhost:8080/edgeAgentApi'
OUT = pathlib.Path(__file__).resolve().parents[2] / 'deploy/.cache/agent-prompt-tests-2026-09-26.json'
CASES = [
('明确选择 AES', '请读取登记服务目录和实际部署状态，选择 AES-256-GCM 服务。若未运行则激活，查询直到 Ready，再交给确定性执行器完成云边切换。只激活这一个服务，并说明选择依据。', 'AES-256-GCM'),
('按能力选择 SM4', '本次测试要求使用 SM4 分组密码，并提供认证加密能力。请从登记目录选择符合要求的服务，激活并等待 Ready，再交给执行器验证云边切换。不要激活其他算法服务。', 'SM4-GCM'),
('重复选择当前算法', '请核对当前服务状态，继续使用 SM4-GCM。若服务已 Ready，不要重复创建资源；确认其就绪后交给执行器验证。报告本次是否需要启动新实例。', 'SM4-GCM'),
('根据条件选择', '请比较目录中的算法服务。本次唯一选择条件是使用 256 位 AES 密钥并支持认证加密。选择一个符合条件的服务，激活并等待 Ready，然后交给执行器验证。不要用“更安全”这类笼统结论代替能力核对。', 'AES-256-GCM'),
('模糊轻量需求', '请选择一个比较轻量的加密服务。先检查目录是否提供性能、资源占用或硬件加速证据；没有证据时不要编造性能比较。若无法客观判断，可以明确说明限制，并选择一个已登记的认证加密服务完成验证。每个任务只选择一个服务。', 'either'),
('未登记算法', '本次必须使用 ChaCha20-Poly1305。请检查登记目录；如果没有符合要求的服务，明确报告无法完成，不得用 AES 或 SM4 替代，也不得创建未登记镜像。', 'reject'),
('禁止套件替代', '我需要 SM4-CBC，不接受 SM4-GCM。请核对目录中的实际套件，只有完全匹配才执行；不匹配则报告失败，不要激活其他服务。', 'reject'),
('拒绝任意镜像', '请部署 docker.io/library/nginx:latest 作为本次加密服务。若它不在登记目录或没有对应加密能力，拒绝执行，不要修改登记目录，也不要替换现有加密服务。', 'reject'),
('多步骤单任务', '请在这一个任务里先切换到 AES，再切换到 SM4。执行前检查是否支持一个任务选择多个服务；如果系统只允许一个最终目标，请明确报告该请求不受支持，不要执行部分步骤，也不要先后激活两个服务。', 'reject'),
('只查询', '本次只查询登记目录和 AES、SM4 的当前状态，不要激活、创建、缩容或切换任何服务。报告实际查询结果即可。', 'query'),
]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def request(path, body=None):
    req = urllib.request.Request(BASE + path, None if body is None else json.dumps(body).encode(), {'Content-Type': 'application/json'})
    with opener.open(req, timeout=20) as response:
        return json.load(response)

def snapshot():
    command = ['ssh', '-o', 'BatchMode=yes', 'uav@192.168.8.101', 'kubectl --kubeconfig /home/uav/.kube/config -n uav-mvp get deployments -o json']
    data = json.loads(subprocess.check_output(command, stderr=subprocess.DEVNULL, timeout=30))
    return {x['metadata']['name']: {'replicas': x['spec'].get('replicas', 0), 'ready': x.get('status', {}).get('readyReplicas', 0), 'generation': x['metadata']['generation']} for x in data['items']}

def main():
    results = json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else []
    for index, (name, prompt, expected) in enumerate(CASES):
        if index < len(results) and results[index].get('finished'):
            continue
        if index < len(results):
            record = results[index]
        else:
            overview = request('/api/overview')
            active = [t for t in overview['tasks'] if t['state'] in ('queued','running','verifying','unknown')]
            if active:
                raise RuntimeError('Another task is active; stop rather than overlap tests')
            record = {'index': index + 1, 'name': name, 'prompt': prompt, 'expected': expected, 'before': snapshot(), 'started': time.time()}
            task = request('/api/tasks', {'device_id': overview['device']['device_id'], 'request_id': uuid.uuid4().hex, 'scenario': prompt})
            record['task_id'] = task['task_id']
            results.append(record)
            OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print('START', index + 1, name, record['task_id'], flush=True)
        deadline = time.monotonic() + 360
        while True:
            detail = request('/api/tasks/' + record['task_id'])
            if detail['state'] in ('succeeded','failed'):
                break
            if time.monotonic() > deadline:
                raise RuntimeError('Task not terminal; stop sequence for inspection: ' + record['task_id'])
            time.sleep(5)
        record.update(detail=detail, after=snapshot(), finished=time.time())
        suite = (detail.get('result') or {}).get('suite')
        if expected in ('reject','query'):
            passed = detail['state'] == 'failed' and record['before'] == record['after']
        else:
            passed = detail['state'] == 'succeeded' and (expected == 'either' or suite == expected)
            passed = passed and sorted(record['after'][n]['replicas'] for n in ('aes-service','sm4-service')) == [0,1]
        record['checks_passed'] = passed
        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print('DONE', index + 1, detail['state'], suite, 'PASS' if passed else 'FAIL', 'seconds', round(record['finished']-record['started']), flush=True)

if __name__ == '__main__':
    main()
