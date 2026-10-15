"""直接验证本地 Swagger 演示服务的 OpenAPI 与 HTTP 行为。"""
import json
import urllib.error
import urllib.request
import uuid

from deploy.scripts.swagger_demo import DEMO_TOKEN


def request(base, path, body=None, authenticated=True, expected=200):
    headers = {'Accept': 'application/json'}
    if authenticated:
        headers['Authorization'] = 'Bearer ' + DEMO_TOKEN
    if body is not None:
        headers['Content-Type'] = 'application/json'
    data = None if body is None else json.dumps(body).encode()
    operation = urllib.request.Request(base + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(operation, timeout=10) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    if status != expected:
        raise AssertionError(f'{path}: expected {expected}, got {status}')
    print(f'{operation.get_method()} {path}: {status}')
    return json.loads(raw) if raw else None


def main():
    cloud, crypto = 'http://127.0.0.1:18080', 'http://127.0.0.1:18081'
    for base in (cloud, crypto):
        schema = request(base, '/openapi.json', authenticated=False)
        assert schema['openapi'].startswith('3.')
        assert schema['components']['securitySchemes']['HTTPBearer']['scheme'] == 'bearer'
    body = {'device_id': 'demo-edge', 'request_id': 'http-' + uuid.uuid4().hex, 'scenario': 'Swagger HTTP 验证'}
    task = request(cloud, '/api/tasks', body)
    assert task['state'] == 'queued'
    assert request(cloud, '/api/tasks', body)['task_id'] == task['task_id']
    request(cloud, '/api/tasks', {**body, 'scenario': '不同任务'}, expected=409)
    assert request(cloud, '/api/tasks/' + task['task_id'])['task_id'] == task['task_id']
    assert any(row['task_id'] == task['task_id'] for row in request(cloud, '/api/overview')['tasks'])
    request(cloud, '/api/overview', authenticated=False, expected=401)
    request(cloud, '/api/edge/exchange', {'kind': 'heartbeat'}, authenticated=False, expected=400)
    request(cloud, '/api/tasks', {'device_id': 'foreign', 'request_id': 'foreign', 'scenario': 'rejected'}, expected=400)
    request(crypto, '/health', authenticated=False)
    request(crypto, '/ready', authenticated=False)
    epoch = 'swagger-' + uuid.uuid4().hex
    assert request(crypto, '/v1/sessions/prepare', {'epoch': epoch})['prepared']
    message = request(crypto, '/v1/seal', {'kind': 'telemetry', 'payload': {'test': 'Swagger'}, 'epoch': epoch})
    assert message['header']['epoch'] == epoch
    assert 'payload' not in message
    request(crypto, '/v1/open', {'envelope': {'tampered': True}}, expected=400)
    request(crypto, '/v1/seal', {'kind': 'heartbeat', 'payload': {}, 'epoch': epoch}, authenticated=False, expected=401)
    request(crypto, '/v1/sessions/retire', {'epoch': epoch})
    request(crypto, '/v1/seal', {'kind': 'heartbeat', 'payload': {}, 'epoch': epoch}, expected=400)
    print('Swagger/OpenAPI 和真实 HTTP 请求验证全部通过。')


if __name__ == '__main__':
    main()
