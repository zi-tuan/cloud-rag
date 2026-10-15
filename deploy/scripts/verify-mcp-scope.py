"""真实 Pi 容器内 MCP 查询；显式命名空间应成功，越界查询应失败。"""
import json
import select
import subprocess
import time

p = subprocess.Popen(['k3s', 'kubectl', 'exec', '-i', '-n', 'uav-mvp',
    'deployment/pi-runtime', '--', '/opt/uav/bin/kubernetes-mcp-server',
    '--cluster-provider', 'in-cluster', '--disable-multi-cluster', '--read-only',
    '--toolsets', 'core'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL, universal_newlines=True, bufsize=1)

def call(i, method, params):
    p.stdin.write(json.dumps(dict(jsonrpc='2.0', id=i, method=method, params=params)) + '\n')
    p.stdin.flush()
    deadline = time.time() + 20
    while time.time() < deadline:
        if select.select([p.stdout], [], [], 1)[0]:
            line = p.stdout.readline()
            if not line:
                raise RuntimeError('MCP exited')
            reply = json.loads(line)
            if reply.get('id') == i:
                return reply.get('result', reply)
    raise RuntimeError('MCP timeout')

try:
    call(1, 'initialize', dict(protocolVersion='2024-11-05', capabilities={}, clientInfo=dict(name='scope-test', version='1')))
    p.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    p.stdin.flush()
    cases = [('pods_list', {}, False),
        ('pods_list_in_namespace', {'namespace':'uav-mvp'}, True),
        ('resources_list', {'apiVersion':'apps/v1','kind':'Deployment','namespace':'uav-mvp'}, True)]
    for i, (name, args, expected) in enumerate(cases, 2):
        result = call(i, 'tools/call', {'name':name, 'arguments':args})
        success = not result.get('isError', False) and 'error' not in result
        print(json.dumps({'tool':name,'arguments':args,'result':result}, ensure_ascii=False), flush=True)
        assert success == expected, 'Unexpected MCP scope result'
finally:
    p.terminate()
    p.wait(timeout=5)
