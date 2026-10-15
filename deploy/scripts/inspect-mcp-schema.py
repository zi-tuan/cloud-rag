"""只读取固定 MCP 版本的工具 schema，不调用任何写工具。"""
import json
import select
import subprocess
import time


def main():
    process = subprocess.Popen([
        '/home/uav/uav-agent-mvp/bin/kubernetes-mcp-server',
        '--kubeconfig', '/home/uav/uav-agent-mvp/mcp/kubeconfig',
        '--disable-multi-cluster', '--toolsets', 'core'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        universal_newlines=True, bufsize=1)
    def call(identifier, method, params):
        process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': identifier, 'method': method, 'params': params}) + '\n')
        process.stdin.flush()
        deadline = time.time() + 15
        while time.time() < deadline:
            if select.select([process.stdout], [], [], 1)[0]:
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError('MCP exited')
                reply = json.loads(line)
                if reply.get('id') == identifier:
                    return reply['result']
        raise RuntimeError('MCP timeout')
    try:
        call(1, 'initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'schema-audit', 'version': '1'}})
        process.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
        process.stdin.flush()
        result = call(2, 'tools/list', {})
        for tool in result['tools']:
            if tool['name'] in ('resources_get', 'resources_scale', 'resources_create_or_update'):
                print(json.dumps(tool, ensure_ascii=False))
    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__ == '__main__':
    main()
