"""FastAPI 迁移的文档、限额、认证和错误契约。"""
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from cloud.cloud_api.api import CloudApplication, create_app as cloud_app
from cloud.cloud_api.tasks import TaskStore
from services.crypto_service.repository.crypto import CounterRepository
from shared.communication.envelope import EnvelopeCodec, MAX_MESSAGE
from services.crypto_service.service.crypto import CryptoService
from services.crypto_service.controller.crypto import create_app as crypto_app


class FastApiContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name)
        (path / 'key').write_bytes(bytes(range(32)))
        (path / 'token').write_text('x' * 32)
        codec = EnvelopeCodec(device_id='edge', key_id='key', root_key=bytes(range(32)), role='cloud',
                              counters=CounterRepository(path / 'cloud.db'), epochs={'demo': 'AES-256-GCM'})
        self.cloud = CloudApplication(TaskStore(path / 'tasks.db'), codec,
                                      login_verifier=lambda auth: auth == 'Bearer ' + 'x' * 32)
        self.crypto = CryptoService({
            'suite': 'AES-256-GCM', 'device_id': 'edge', 'key_id': 'key', 'role': 'edge',
            'key_file': str(path / 'key'), 'token_file': str(path / 'token'),
            'state_path': str(path / 'crypto.db'), 'initialize': True, 'initial_epoch': 'demo',
        })
        self.headers = {'Authorization': 'Bearer ' + 'x' * 32}

    def test_swagger_schema_lists_all_routes_and_bearer_auth(self):
        for app, paths in ((cloud_app(self.cloud), {'/api/recovery', '/api/auth/recovery', '/api/overview', '/api/flights', '/api/tasks', '/api/tasks/{task_id}', '/api/edge/exchange', '/api/auth/start', '/api/auth/respond', '/api/auth/confirm', '/api/auth/revoke', '/api/missions', '/api/missions/{mission_id}'}),
                           (crypto_app(self.crypto, 'x' * 32), {'/health', '/ready', '/v1/seal', '/v1/open', '/v1/sessions/lease', '/v1/sessions/prepare', '/v1/sessions/retire', '/v1/sessions/retirement'})):
            with TestClient(app) as client:
                self.assertIn('SwaggerUIBundle', client.get('/docs').text)
                self.assertEqual(client.get('/redoc').status_code, 200)
                schema = client.get('/openapi.json').json()
                self.assertEqual(set(schema['paths']), paths)
                self.assertEqual(schema['components']['securitySchemes']['HTTPBearer']['scheme'], 'bearer')
                for path, methods in schema['paths'].items():
                    if path.startswith('/api/') and path not in ('/api/auth/recovery', '/api/edge/exchange', '/api/auth/start', '/api/auth/respond', '/api/auth/confirm') or path.startswith('/v1/'):
                        self.assertTrue(next(iter(methods.values()))['security'])

    def test_invalid_body_size_and_auth_fail_closed(self):
        for app, route, oversized_status in ((cloud_app(self.cloud), '/api/tasks', 400),
                                              (crypto_app(self.crypto, 'x' * 32), '/v1/seal', 413)):
            with TestClient(app) as client:
                self.assertEqual(client.post(route, json={}, headers=self.headers).status_code, 400)
                self.assertEqual(client.post(route, content='{', headers=self.headers).status_code, 400)
                self.assertEqual(client.post(route, content=b'x' * (MAX_MESSAGE * 2 + 1), headers=self.headers).status_code, oversized_status)
                self.assertEqual(client.post(route, json={}).status_code, 401)
                self.assertEqual(client.post(route, json=[], headers=self.headers).status_code, 400)

    def test_console_conflict_device_isolation_and_login_outage(self):
        with TestClient(cloud_app(self.cloud)) as client:
            body = {'device_id': 'edge', 'request_id': 'swagger', 'scenario': '测试'}
            first = client.post('/api/tasks', json=body, headers=self.headers)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(client.post('/api/tasks', json=body, headers=self.headers).json(), first.json())
            self.assertEqual(client.post('/api/tasks', json={**body, 'scenario': '另一个任务'}, headers=self.headers).status_code, 409)
            self.assertEqual(client.post('/api/tasks', json={**body, 'device_id': 'foreign'}, headers=self.headers).status_code, 400)
            self.assertEqual(client.get('/api/tasks/missing', headers=self.headers).status_code, 404)
            self.cloud.verify_login = lambda auth: None
            self.assertEqual(client.get('/api/overview', headers=self.headers).status_code, 503)


if __name__ == '__main__':
    unittest.main()
