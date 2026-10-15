import tempfile
import threading
import time
import unittest
import urllib.error
from http_server import LiveServer
from pathlib import Path

from shared.communication.crypto_client import CryptoServiceCodec
from services.crypto_service.repository.crypto import CounterRepository
from shared.communication.envelope import EnvelopeCodec, ProtocolError
from shared.communication.switch import SwitchState
from services.crypto_service.service.crypto import CryptoService
from services.crypto_service.controller.crypto import create_app


class CryptoClientTests(unittest.TestCase):
    def test_retirement_rpc_outage_does_not_block_committed_business_after_restart(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)
            now = [1000.0]
            def client():
                codec = CryptoServiceCodec(device_id='edge', key_id='key', role='edge',
                    services={'AES-256-GCM': 'http://127.0.0.1:1', 'SM4-GCM': 'http://127.0.0.1:2'}, token='x' * 32)
                def request(suite, route, body=None):
                    if route == '/v1/sessions/retirement':
                        raise OSError('old service unavailable')
                    if route == '/v1/sessions/prepare':
                        return {'epoch': body['epoch'], 'suite': suite, 'prepared': True}
                    if route == '/v1/sessions/retire':
                        raise OSError('old service unavailable')
                codec.request = request
                return codec
            codec = client()
            state = SwitchState(path / 'switch.db', codec, 'old', 'AES-256-GCM', initialize=True, clock=lambda: now[0])
            record = state.begin({'device_id':'edge','request_id':'change','old_epoch':'old',
                'epoch':'new','suite':'SM4-GCM','protection_profile':'enhanced'})
            record.update(state='committed', retire_at=1010.0)
            state.save(record, active='new')
            fresh = client()
            restarted = SwitchState(path / 'switch.db', fresh, 'old', 'AES-256-GCM', clock=lambda: now[0])
            self.assertEqual(fresh.session('new'), 'SM4-GCM')
            now[0] = 1011.0
            restarted.sync_codec()
            with self.assertRaises(ProtocolError):
                fresh.session('old')
            self.assertEqual(fresh.session('new'), 'SM4-GCM')

    def test_physical_retirement_preserves_original_deadline_for_lost_ack_retry(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)
            (path / 'key').write_bytes(bytes(range(32)))
            now = [1000.0]
            config = {'suite':'AES-256-GCM','device_id':'edge','key_id':'key','role':'edge',
                'key_file':str(path / 'key'),'state_path':str(path / 'crypto.db'),'initialize':True}
            service = CryptoService(config, clock=lambda: now[0])
            service.lease('old', 'lightweight', 1900)
            service.prepare('old')
            service.schedule_retirement('old', 1010.0)
            service.retire('old')
            restarted = CryptoService(config, clock=lambda: now[0])
            restarted.schedule_retirement('old', 1010.0)
            with self.assertRaises(ProtocolError):
                restarted.prepare('old')
            with self.assertRaises(ProtocolError):
                restarted.schedule_retirement('old', 1011.0)

    def test_unacknowledged_retirement_plan_replays_original_deadline_after_restart(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)
            (path / 'key').write_bytes(bytes(range(32)))
            now = [time.time()]
            service = CryptoService({'suite': 'AES-256-GCM', 'device_id': 'edge', 'key_id': 'key', 'role': 'edge',
                'key_file': str(path / 'key'), 'state_path': str(path / 'crypto.db'), 'initialize': True}, clock=lambda: now[0])
            server = LiveServer(create_app(service, 'x' * 32))
            server.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = 'http://127.0.0.1:' + str(server.server_port)
            def client():
                return CryptoServiceCodec(device_id='edge', key_id='key', role='edge',
                    services={'AES-256-GCM': url, 'SM4-GCM': url}, token='x' * 32)
            codec = client()
            state = SwitchState(path / 'switch.db', codec, 'old', 'AES-256-GCM', initialize=True, clock=lambda: now[0])
            codec.lease('old', 'lightweight', int(now[0]) + 900)
            record = state.begin({'device_id':'edge','request_id':'change','old_epoch':'old',
                'epoch':'new','suite':'AES-256-GCM','protection_profile':'enhanced'})
            codec.lease('new', 'enhanced', int(now[0]) + 120)
            record.update(state='committed', retire_at=now[0] + 10)
            def unavailable(*args):
                raise OSError('retirement acknowledgement unavailable')
            codec.schedule_retirement = unavailable
            state.save(record, active='new')
            fresh_codec = client()
            replayed = []
            schedule = fresh_codec.schedule_retirement
            def replay(*args):
                replayed.append(args)
                return schedule(*args)
            fresh_codec.schedule_retirement = replay
            restarted = SwitchState(path / 'switch.db', fresh_codec, 'old', 'AES-256-GCM', clock=lambda: now[0])
            self.assertEqual(replayed, [('old', 'AES-256-GCM', record['retire_at'])])
            self.assertEqual(restarted.active(), 'new')
            restarted.sync_codec()
            self.assertEqual(len(replayed), 1, '已确认的退役计划不应重复调用服务')
            now[0] = record['retire_at']
            with self.assertRaises(urllib.error.HTTPError):
                fresh_codec.request('AES-256-GCM', '/v1/seal', {'kind':'heartbeat','payload':{},'epoch':'old'})
            self.assertEqual(fresh_codec.seal('heartbeat', {}, epoch='new')['header']['epoch'], 'new')

    def test_scheduled_retirement_is_enforced_after_controller_and_service_restart(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)
            (path / 'key').write_bytes(bytes(range(32)))
            now = [time.time()]
            config = {'suite': 'AES-256-GCM', 'device_id': 'edge', 'key_id': 'key', 'role': 'edge',
                'key_file': str(path / 'key'), 'state_path': str(path / 'crypto.db'), 'initialize': True}
            service = CryptoService(config, clock=lambda: now[0])
            server = LiveServer(create_app(service, 'x' * 32))
            server.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = 'http://127.0.0.1:' + str(server.server_port)
            codec = CryptoServiceCodec(device_id='edge', key_id='key', role='edge',
                services={'AES-256-GCM': url, 'SM4-GCM': url}, token='x' * 32)
            codec.allow_epoch('old', 'AES-256-GCM')
            original_expires = int(now[0]) + 900
            codec.lease('old', 'lightweight', original_expires)
            codec.seal('heartbeat', {}, epoch='old')
            peer = EnvelopeCodec(device_id='edge', key_id='key', role='cloud', root_key=bytes(range(32)),
                counters=CounterRepository(path / 'peer.db'), epochs={'old': 'AES-256-GCM'})
            unseen = peer.seal('business.reply', {'ok': True}, epoch='old')
            deadline = now[0] + 5
            codec.schedule_retirement('old', 'AES-256-GCM', deadline)
            codec.seal('heartbeat', {}, epoch='old')
            with self.assertRaises(urllib.error.HTTPError):
                codec.schedule_retirement('old', 'AES-256-GCM', deadline + 1)
            server.shutdown()
            server.server_close()
            restarted = CryptoService(config, clock=lambda: now[0])
            server = LiveServer(create_app(restarted, 'x' * 32))
            server.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            restarted_codec = CryptoServiceCodec(device_id='edge', key_id='key', role='edge',
                services={'AES-256-GCM': 'http://127.0.0.1:' + str(server.server_port), 'SM4-GCM': url}, token='x' * 32)
            now[0] = deadline
            self.assertLess(now[0], original_expires)
            for endpoint, body in [
                ('/v1/seal', {'kind': 'heartbeat', 'payload': {}, 'epoch': 'old'}),
                ('/v1/open', {'envelope': unseen}),
                ('/v1/sessions/prepare', {'epoch': 'old'}),
            ]:
                with self.subTest(endpoint=endpoint), self.assertRaises(urllib.error.HTTPError) as rejected:
                    restarted_codec.request('AES-256-GCM', endpoint, body)
                self.assertEqual(rejected.exception.code, 400)

    def test_preparation_is_once_per_epoch_but_expiry_and_retirement_are_checked_on_every_operation(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)
            (path / 'key').write_bytes(bytes(range(32)))
            now = [time.time()]
            service = CryptoService({'suite': 'AES-256-GCM', 'device_id': 'edge', 'key_id': 'key', 'role': 'edge',
                'key_file': str(path / 'key'), 'state_path': str(path / 'crypto.db'), 'initialize': True}, clock=lambda: now[0])
            server = LiveServer(create_app(service, 'x' * 32))
            server.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = 'http://127.0.0.1:' + str(server.server_port)
            codec = CryptoServiceCodec(device_id='edge', key_id='key', role='edge',
                services={'AES-256-GCM': url, 'SM4-GCM': url}, token='x' * 32)
            peer = EnvelopeCodec(device_id='edge', key_id='key', role='cloud', root_key=bytes(range(32)),
                counters=CounterRepository(path / 'peer.db'), epochs={'first': 'AES-256-GCM', 'second': 'AES-256-GCM'})
            preparations = []
            original = service.prepare
            def prepare(epoch):
                preparations.append(epoch)
                return original(epoch)
            service.prepare = prepare
            for epoch in ('first', 'second'):
                codec.allow_epoch(epoch, 'AES-256-GCM')
                codec.lease(epoch, 'lightweight', int(now[0]) + 60)
                for _ in range(3):
                    request = codec.seal('heartbeat', {}, epoch=epoch)
                    peer.open(request)
                    response = peer.seal('business.reply', {'ok': True}, epoch=epoch, reply_to=request['header']['message_id'])
                    self.assertTrue(codec.open(response)[1]['ok'])
                self.assertEqual(preparations.count(epoch), 1, '每次业务不应重新准备同一个 epoch')
            codec.request('AES-256-GCM', '/v1/sessions/retire', {'epoch': 'first'})
            with self.assertRaises(urllib.error.HTTPError):
                codec.seal('heartbeat', {}, epoch='first')
            now[0] += 61
            with self.assertRaises(urllib.error.HTTPError):
                codec.seal('heartbeat', {}, epoch='second')
            response = peer.seal('business.reply', {'ok': True}, epoch='second')
            with self.assertRaises(ProtocolError):
                codec.open(response)

    def test_real_http_services_hold_keys_and_enforce_epoch_retirement(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)
            (path / 'key').write_bytes(bytes(range(32)))
            (path / 'token').write_text('x' * 32)
            services = {}
            servers = []
            try:
                for suite in ('AES-256-GCM', 'SM4-GCM'):
                    app = CryptoService({'suite': suite, 'device_id': 'edge', 'key_id': 'key', 'role': 'edge',
                                             'key_file': str(path / 'key'), 'token_file': str(path / 'token'),
                                             'state_path': str(path / (suite + '.db')), 'initialize': True})
                    server = LiveServer(create_app(app, 'x' * 32))
                    server.start()
                    thread = server.thread
                    servers.append((server, thread))
                    services[suite] = 'http://127.0.0.1:' + str(server.server_port)
                edge = CryptoServiceCodec(device_id='edge', key_id='key', role='edge', services=services, token='x' * 32)
                cloud = EnvelopeCodec(device_id='edge', key_id='key', role='cloud', root_key=bytes(range(32)),
                                      counters=CounterRepository(path / 'cloud.db'), epochs={'aes': 'AES-256-GCM', 'sm4': 'SM4-GCM'})
                for epoch, suite in [('aes', 'AES-256-GCM'), ('sm4', 'SM4-GCM')]:
                    self.assertTrue(edge.ready(suite))
                    edge.allow_epoch(epoch, suite)
                    edge.lease(epoch,'lightweight',int(time.time())+900)
                    request = edge.seal('heartbeat', {'trust_start': 'disabled'}, epoch=epoch)
                    self.assertEqual(cloud.open(request)[1]['trust_start'], 'disabled')
                    response = cloud.seal('business.reply', {'ok': True}, epoch=epoch,
                                          reply_to=request['header']['message_id'])
                    self.assertTrue(edge.open(response)[1]['ok'])
                    with self.assertRaises(ProtocolError):
                        edge.open(response)
                    unseen = cloud.seal('business.reply', {'ok': True}, epoch=epoch)
                    edge.retire_epoch(epoch)
                    with self.assertRaises(ProtocolError):
                        edge.seal('heartbeat', {}, epoch=epoch)
                    # 绕过客户端缓存，直接调用实际密码服务；租约尚未到期。
                    for endpoint, body in [
                        ('/v1/seal', {'kind': 'heartbeat', 'payload': {}, 'epoch': epoch}),
                        ('/v1/open', {'envelope': unseen}),
                        ('/v1/sessions/prepare', {'epoch': epoch}),
                    ]:
                        with self.subTest(suite=suite, endpoint=endpoint):
                            with self.assertRaises(urllib.error.HTTPError) as rejected:
                                edge.request(suite, endpoint, body)
                            self.assertEqual(rejected.exception.code, 400)
            finally:
                for server, thread in servers:
                    server.shutdown()
                    thread.join()
                    server.server_close()

    def test_retirement_failure_blocks_old_epoch_and_can_retry_without_blocking_new_epoch(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)
            (path / 'key').write_bytes(bytes(range(32)))
            service = CryptoService({'suite': 'AES-256-GCM', 'device_id': 'edge', 'key_id': 'key', 'role': 'edge',
                'key_file': str(path / 'key'), 'state_path': str(path / 'crypto.db'), 'initialize': True})
            server = LiveServer(create_app(service, 'x' * 32))
            server.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = 'http://127.0.0.1:' + str(server.server_port)
            codec = CryptoServiceCodec(device_id='edge', key_id='key', role='edge',
                services={'AES-256-GCM': url, 'SM4-GCM': url}, token='x' * 32)
            for epoch in ('old', 'new'):
                codec.allow_epoch(epoch, 'AES-256-GCM')
                codec.lease(epoch, 'lightweight', int(time.time()) + 60)
                codec.seal('heartbeat', {}, epoch=epoch)
            request = codec.request
            def unavailable(suite, endpoint, body=None):
                if endpoint == '/v1/sessions/retire':
                    raise OSError('temporary outage')
                return request(suite, endpoint, body)
            codec.request = unavailable
            with self.assertRaises(OSError):
                codec.retire_epoch('old')
            with self.assertRaises(ProtocolError):
                codec.seal('heartbeat', {}, epoch='old')
            self.assertEqual(codec.seal('heartbeat', {}, epoch='new')['header']['epoch'], 'new')
            codec.request = request
            codec.retire_epoch('old')
            with self.assertRaises(urllib.error.HTTPError):
                codec.request('AES-256-GCM', '/v1/seal', {'kind': 'heartbeat', 'payload': {}, 'epoch': 'old'})


if __name__ == '__main__':
    unittest.main()
