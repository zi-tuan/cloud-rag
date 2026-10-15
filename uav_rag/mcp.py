"""Bounded stdio MCP transport, matching the original UAV guard's protocol."""
import json
import sys

from .tools import KnowledgeError, RagTools, environment_client


def serve(guard):
    while True:
        # Preserve the original guard's input ceiling for non-RAG tools.
        line = sys.stdin.readline(1024 * 1024 + 1)
        if not line or len(line) > 1024 * 1024:
            return
        identifier = None
        try:
            message = json.loads(line)
            if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                raise ValueError('invalid JSON-RPC request')
            if 'id' not in message:
                continue
            identifier = message['id']
            method = message.get('method')
            if method == 'initialize':
                result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'uav-cloud-knowledge', 'version': '1'}}
            elif method == 'tools/list':
                result = {'tools': guard.tools()}
            elif method == 'tools/call':
                params = message.get('params', {})
                if not isinstance(params, dict):
                    raise ValueError('invalid params')
                try:
                    value = guard.invoke(params['name'], params.get('arguments', {}))
                    # Match the compact serialization used to enforce retrieval budgets.
                    from .retrieval import encoded
                    result = {'content': [{'type': 'text', 'text': encoded(value).decode()}]}
                except KnowledgeError as exc:
                    result = {'isError': True, 'content': [{'type': 'text', 'text': str(exc)}]}
                except (OSError, ValueError, KeyError, TypeError):
                    result = {'isError': True, 'content': [{'type': 'text', 'text':
                        'Tool unavailable or invalid arguments; do not infer success. Check query/schema/connectivity; missing knowledge must be reported.'}]}
            elif method == 'ping':
                result = {}
            else:
                raise ValueError('unsupported method')
            response = {'jsonrpc': '2.0', 'id': identifier, 'result': result}
        except (OSError, ValueError, KeyError, TypeError):
            response = {'jsonrpc': '2.0', 'id': identifier,
                        'error': {'code': -32602, 'message': 'Invalid or unavailable operation'}}
        print(json.dumps(response, ensure_ascii=False), flush=True)


def main():
    serve(RagTools(environment_client()))


if __name__ == '__main__':
    main()
