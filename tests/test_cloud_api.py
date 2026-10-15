import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http_server import LiveServer
from pathlib import Path

from cloud.cloud_api.api import CloudApplication, create_app
from cloud.cloud_api.tasks import TaskStore
from services.crypto_service.repository.crypto import CounterRepository
from shared.communication.envelope import EnvelopeCodec


class CloudApiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name)
        def codec(role):
            return EnvelopeCodec(device_id='edge', key_id='identity', root_key=bytes(range(32)),
                                 role=role, counters=CounterRepository(path / (role + '.db')),
                                 epochs={'initial': 'AES-256-GCM', 'next': 'SM4-GCM'})
        self.edge = codec('edge')
        self.store = TaskStore(path / 'tasks.db')
        self.app = CloudApplication(self.store, codec('cloud'), login_verifier=lambda auth: auth == 'Bearer ' + 'x' * 32)
        self.server = LiveServer(create_app(self.app))
        self.server.start()
        thread = self.server.thread
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def post(self, path, body, authenticated=False):
        headers = {'Content-Type': 'application/json'}
        if authenticated:
            headers['Authorization'] = 'Bearer ' + 'x' * 32
        request = urllib.request.Request(self.base + path, json.dumps(body).encode(), headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def exchange(self, kind, body, epoch):
        envelope = self.edge.seal(kind, body, epoch=epoch)
        response = self.post('/api/edge/exchange', envelope)
        header, result = self.edge.open(response)
        self.assertEqual(header['reply_to'], envelope['header']['message_id'])
        self.assertEqual(header['epoch'], epoch)
        self.assertNotIn('scenario', response)
        return result

    def test_encrypted_business_in_both_suites(self):
        for epoch in ('initial', 'next'):
            task = self.post('/api/tasks', {'device_id': 'edge', 'request_id': epoch,
                                          'scenario': '仿真巡检通信切换'}, authenticated=True)
            claim = self.exchange('task.claim', {}, epoch)
            self.assertEqual(claim['result']['task']['task_id'], task['task_id'])
            for kind in ('heartbeat', 'telemetry', 'status'):
                self.assertTrue(self.exchange(kind, {'trust_start': 'disabled'}, epoch)['ok'])
            event = {'task_id': task['task_id'], 'event_id': epoch, 'state': 'failed',
                     'result': {'reason': 'verification not yet implemented'}}
            self.assertTrue(self.exchange('task.event', event, epoch)['ok'])

    def test_plaintext_tamper_replay_and_unauthenticated_console_rejected(self):
        for route, body in [('/api/edge/exchange', {'kind': 'task.claim'}),
                            ('/api/tasks', {'device_id': 'edge'})]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post(route, body)
            self.assertEqual(error.exception.read(), b'')
        message = self.edge.seal('heartbeat', {}, epoch='initial')
        self.post('/api/edge/exchange', message)
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/edge/exchange', message)
        message = self.edge.seal('heartbeat', {}, epoch='initial')
        message['header']['kind'] = 'task.claim'
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/edge/exchange', message)

    def test_authenticated_business_errors_are_encrypted(self):
        response = self.exchange('task.event', {'state': 'succeeded'}, 'initial')
        self.assertFalse(response['ok'])
        self.assertEqual(response['error'], 'invalid_business_request')

    def test_console_overview_and_history_require_web_login(self):
        task = self.store.submit('edge', 'console-1', '创建 SM4 服务')
        self.store.claim('edge')
        self.store.event('edge', task['task_id'], 'event-1', 'verifying', {'ready': True})
        self.store.submit('other', 'foreign', 'must not be listed')
        for route in ('/api/overview', '/api/tasks/' + task['task_id']):
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.base + route)
            self.assertEqual(error.exception.code, 401)
            request = urllib.request.Request(self.base + route, headers={'Authorization': 'Bearer ' + 'x' * 32})
            with urllib.request.urlopen(request) as response:
                data = json.load(response)
            if route == '/api/overview':
                self.assertEqual([row['task_id'] for row in data['tasks']], [task['task_id']])
                self.assertFalse(data['device']['online'])
            else:
                self.assertEqual(data['events'][0]['state'], 'verifying')
                self.assertEqual(data['state'], 'verifying')


if __name__ == '__main__':
    unittest.main()
