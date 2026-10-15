import tempfile
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from shared.adapters.trust import TrustAdapter
from cloud.cloud_api.api import CloudApplication
from cloud.cloud_api.tasks import TaskStore
from services.crypto_service.repository.crypto import CounterRepository
from shared.communication.envelope import EnvelopeCodec, ProtocolError
from jetson.edge_agent.client import EdgeClient


class EdgeClientTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name)
        def codec(role):
            return EnvelopeCodec(device_id='edge', key_id='key', root_key=bytes(32), role=role,
                                 counters=CounterRepository(path / (role + '.db')), epochs={'aes': 'AES-256-GCM', 'sm4': 'SM4-GCM'})
        self.edge, self.cloud = codec('edge'), codec('cloud')
        self.app = CloudApplication(TaskStore(path / 'tasks.db'), self.cloud,
                                    login_verifier=lambda auth: auth == 'Bearer ' + 't' * 32)
        self.epoch = 'aes'

    def client(self, transport=None):
        return EdgeClient('http://cloud.example/api/edge/exchange', self.edge, lambda: self.epoch,
                          transport=transport or self.app.exchange)

    def test_disabled_trust_still_authenticates_business(self):
        client = self.client()
        for epoch in ('aes', 'sm4'):
            self.epoch = epoch
            self.assertTrue(client.exchange('heartbeat', {'trust_start': TrustAdapter().status()})['accepted'])
        self.assertEqual(TrustAdapter().status(), 'unknown')
        self.assertEqual(TrustAdapter(enabled=True).status(), 'unknown')
        self.assertEqual(TrustAdapter(enabled=True, measure=lambda: True).status(), 'unknown')

    def test_response_from_other_request_or_epoch_is_rejected(self):
        def wrong_reply(request):
            return self.cloud.seal('business.reply', {'ok': True, 'result': {}}, epoch='aes', reply_to='other')
        with self.assertRaises(ProtocolError):
            self.client(wrong_reply).exchange('heartbeat', {})
        def wrong_epoch(request):
            return self.cloud.seal('business.reply', {'ok': True, 'result': {}}, epoch='sm4',
                                   reply_to=request['header']['message_id'])
        with self.assertRaises(ProtocolError):
            self.client(wrong_epoch).exchange('heartbeat', {})

    def test_disconnect_does_not_retry_or_fallback(self):
        calls = []
        def disconnected(message):
            calls.append(message)
            raise TimeoutError('offline')
        with self.assertRaises(TimeoutError):
            self.client(disconnected).exchange('task.claim', {})
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(calls[0]), {'header', 'ciphertext', 'tag'})
        with self.assertRaises(ValueError):
            EdgeClient('http://user:password@cloud.example', self.edge, lambda: 'aes')

    def test_http_transports_encrypted_business_without_certificates(self):
        app = self.app
        messages = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                messages.append(request)
                reply = json.dumps(app.exchange(request)).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *args):
                pass

        server = HTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.handle_request)
        worker.start()
        try:
            client = EdgeClient(f'http://127.0.0.1:{server.server_port}/api/edge/exchange',
                                self.edge, lambda: 'aes', timeout=2)
            self.assertTrue(client.exchange('heartbeat', {'trust_start': 'disabled'})['accepted'])
            self.assertEqual(set(messages[0]), {'header', 'ciphertext', 'tag'})
        finally:
            worker.join(timeout=3)
            server.server_close()


if __name__ == '__main__':
    unittest.main()
