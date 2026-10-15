"""Deterministic substitute Agent; exercises actual subprocess MCP, not an LLM."""
import json
import os
from pathlib import Path
import subprocess
import sys

def main():
    prompt = sys.stdin.read()
    process = subprocess.Popen([sys.executable, '-m', 'tools.fixture_guard'], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    sequence = 0
    trace = []
    def rpc(method, params=None):
        nonlocal sequence
        sequence += 1
        process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': sequence, 'method': method, 'params': params or {}})+'\n')
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        assert response['id'] == sequence and 'error' not in response
        return response['result']
    def call(name, arguments):
        result = rpc('tools/call', {'name': name, 'arguments': arguments})
        trace.append({'tool': name, 'arguments': arguments, 'result': result})
        return result
    try:
        rpc('initialize')
        names = [t['name'] for t in rpc('tools/list')['tools']]
        assert 'uav_knowledge_query' in names and 'uav_combination_select' in names
        call('uav_services', {})
        call('uav_authentication_policies', {})
        target = {'auth_policy': 'Ed25519', 'suite': 'SM4-GCM', 'protection_profile': 'lightweight'}
        assert call('uav_combination_select', target).get('isError')
        citations = []
        pages = []
        for service in ('auth-ed25519', 'sm4-gcm-v1'):
            args = {'query': '签名 加密 用途 限制', 'candidate_services': [service], 'top_k': 1}
            while True:
                reply = call('uav_knowledge_query', args.copy())
                assert not reply.get('isError'), reply
                page = json.loads(reply['content'][0]['text'])
                pages.append(page)
                found = [item['id'] for item in page['items'] if item['kind'] == 'citation']
                if found:
                    citations.append(found[0])
                    if service == 'auth-ed25519' and page['next_cursor']:
                        args['cursor'] = page['next_cursor']
                        continuation = call('uav_knowledge_query', args.copy())
                        assert not continuation.get('isError'), continuation
                        pages.append(json.loads(continuation['content'][0]['text']))
                    break
                assert page['next_cursor'], 'Insufficient evidence; do not select'
                args['cursor'] = page['next_cursor']
        assert call('uav_combination_select', {**target, 'citation_ids': ['forged']}).get('isError')
        reply = call('uav_combination_select', {**target, 'citation_ids': citations})
        assert not reply.get('isError'), reply
        result = {'agent': 'deterministic substitute, no LLM', 'kubernetes': 'simulated orchestrator',
                  'prompt': prompt, 'tools': names, 'trace': trace, 'pages': pages,
                  'selection': json.loads(reply['content'][0]['text']), 'citation_ids': citations,
                  'mcp_pid': process.pid}
        Path(os.environ['UAV_SELECTION_FILE']).with_suffix('.trace.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('已查询并提交选择；尚待确定性执行器验证。本进程为替身 Agent。')
    finally:
        process.stdin.close()
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()

if __name__ == '__main__':
    main()
