import copy
import json
import secrets
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from cloud.cloud_api.api import CloudApplication, create_app
from cloud.cloud_api.auth import CloudAuth
from cloud.cloud_api.tasks import TaskStore
from shared.communication.auth_protocol import Ed25519Provider
from services.crypto_service.repository.crypto import CounterRepository
from shared.communication.envelope import EnvelopeCodec, ProtocolError
from shared.communication.switch import CloudSwitch, SwitchState
from jetson.edge_agent.auth import AuthenticatedEdgeClient, EdgeAuth
from jetson.edge_agent.client import EdgeClient
from jetson.edge_agent.switch import SwitchCoordinator
from services.auth_service.app import create_app as auth_app
from shared.communication.auth_client import AuthServiceClient
from shared.communication.crypto_client import CryptoServiceCodec
from services.crypto_service.service.crypto import CryptoService
from services.crypto_service.controller.crypto import create_app as crypto_app
from http_server import LiveServer


class AuthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.now = 1000
        self.providers = {}
        for role in ('edge', 'cloud'):
            key = Ed25519PrivateKey.generate()
            (self.path / (role + '.pem')).write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            (self.path / (role + '.pub')).write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        for role, peer in (('edge', 'cloud'), ('cloud', 'edge')):
            config = {'role': role, 'device_id': 'jetson', 'cloud_id': 'control', 'credential_version': '1',
                      'private_key_file': str(self.path / (role + '.pem')), 'peer_public_key_file': str(self.path / (peer + '.pub')),
                      'not_before': 0, 'not_after': 100000}
            self.providers[role] = Ed25519Provider(config, clock=lambda: self.now)
        self.states = {}
        for role in ('cloud', 'edge'):
            codec = EnvelopeCodec(device_id='jetson', key_id='key', root_key=bytes(range(32)), role=role,
                                  counters=CounterRepository(self.path / (role + '.db')), epochs={}, clock=lambda: self.now)
            self.states[role] = SwitchState(self.path / (role + '-switch.db'), codec, 'aes0', 'AES-256-GCM', initialize=True, clock=lambda: self.now)
        self.cloud = CloudAuth(self.path / 'auth.db', self.providers['cloud'], self.states['cloud'].codec,
                               cloud_id='control', clock=lambda: self.now)
        self.app = CloudApplication(TaskStore(self.path / 'tasks.db'), self.states['cloud'].codec,
                                    switch=CloudSwitch(self.states['cloud'], lambda suite: True),
                                    login_verifier=lambda token: token == 'Bearer ' + 'x' * 32, auth=self.cloud)
        self.edge = EdgeAuth(self.states['edge'].codec, self.providers['edge'], cloud_id='control', clock=lambda: self.now)
        self.wire = []
        self.drop = None
        def transport(message):
            self.wire.append(copy.deepcopy(message))
            answer = self.app.exchange(message)
            if message['header']['kind'] == self.drop:
                self.drop = None
                raise TimeoutError('injected reply loss')
            return answer
        self.client = AuthenticatedEdgeClient('http://cloud.example/api/edge/exchange', self.states['edge'].codec,
                                              lambda: self.coordinator.business_epoch(), self.edge,
                                              transport=transport, auth_transport=lambda op, body: self.app.recovery.offer(body) if op == 'recovery' else getattr(self.cloud, op)(body))
        self.coordinator = SwitchCoordinator(self.states['edge'], self.client, lambda suite: True)

    def start(self):
        return self.cloud.start({'protection_profile': 'lightweight', 'device_id': 'jetson', 'policy': 'Ed25519', 'credential_version': '1',
                                 'key_id': 'key', 'epoch': 'aes0', 'suite': 'AES-256-GCM',
                                 'client_nonce': secrets.token_hex(32), 'request_id': secrets.token_hex(16)})

    def test_mutual_auth_and_aes_sm4_switch(self):
        self.assertTrue(self.client.exchange('heartbeat', {})['accepted'])
        result = self.coordinator.start('change1', 'sm4-1', 'SM4-GCM')
        self.assertEqual(result['state'], 'succeeded')
        self.assertTrue(self.client.exchange('telemetry', {'read_only': True, 'sampled_at': self.now})['accepted'])
        self.assertEqual({x['header']['suite'] for x in self.wire}, {'AES-256-GCM', 'SM4-GCM'})
        self.assertTrue(all(x['header']['v'] == 2 and 'identity_proof' in x for x in self.wire))
        self.assertTrue(self.cloud.status()['bidirectional_confirmation'])

    def test_root_key_and_observed_session_cannot_impersonate(self):
        self.client.exchange('heartbeat', {})
        observed = self.wire[-1]
        attacker = EnvelopeCodec(device_id='jetson', key_id='key', root_key=bytes(range(32)), role='edge',
                                 counters=CounterRepository(self.path / 'attacker.db'), epochs={'aes0': 'AES-256-GCM'})
        message = attacker.seal('task.claim', {}, epoch='aes0', auth_session_id=observed['header']['auth_session_id'])
        with self.assertRaises(ProtocolError):
            self.app.exchange(message)
        message['identity_proof'] = observed['identity_proof']
        with self.assertRaises(ProtocolError):
            self.app.exchange(message)
        with self.assertRaises(ProtocolError):
            self.app.exchange(attacker.seal('heartbeat', {}, epoch='aes0'))

    def test_overview_uses_active_epoch_not_newest_candidate_authentication(self):
        self.client.exchange('heartbeat', {})
        binding = {'protection_profile': 'lightweight', 'device_id': 'jetson', 'request_id': 'candidate', 'old_epoch': 'aes0',
                   'epoch': 'sm4-candidate', 'suite': 'SM4-GCM'}
        for state in self.states.values():
            state.begin(binding)
        # 候选已完成身份认证，但加密切换尚未提交，不能展示成当前组合。
        self.edge.ensure(self.client, 'sm4-candidate')
        self.assertEqual(self.cloud.status()['epoch'], 'sm4-candidate')
        api = TestClient(create_app(self.app))
        headers = {'Authorization': 'Bearer ' + 'x' * 32}
        result = api.get('/api/overview', headers=headers).json()
        self.assertEqual(result['server_time'], self.now)
        self.assertEqual(result['communication']['suite'], 'AES-256-GCM')
        self.assertEqual(result['identity_auth']['epoch'], 'aes0')
        self.assertEqual(result['identity_auth']['suite'], 'AES-256-GCM')
        self.cloud.revoke(self.edge.active['aes0'])
        result = api.get('/api/overview', headers=headers).json()
        self.assertFalse(result['identity_auth']['authenticated'])

    def test_signed_message_replay_and_tampering_rejected(self):
        self.client.exchange('heartbeat', {})
        with self.assertRaises(ProtocolError):
            self.app.exchange(self.wire[-1])
        for field, value in [('auth_session_id', 'a' * 64), ('device_id', 'other'), ('key_id', 'other'),
                             ('epoch', 'other'), ('suite', 'SM4-GCM'), ('direction', 'cloud-to-edge')]:
            message = self.client.codec.seal('heartbeat', {}, epoch='aes0')
            message['header'][field] = value
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                self.app.exchange(message)

    def test_challenge_single_use_and_failed_proof_consumed(self):
        answer = self.start()
        transcript = answer['transcript']
        body = {'auth_session_id': transcript['auth_session_id'], 'proof': self.providers['edge'].prove('edge-response', transcript)}
        self.cloud.respond(body)
        with self.assertRaises(ProtocolError):
            self.cloud.respond(body)
        answer = self.start()
        transcript = answer['transcript']
        with self.assertRaises(ProtocolError):
            self.cloud.respond({'auth_session_id': transcript['auth_session_id'], 'proof': 'bad'})
        with self.assertRaises(ProtocolError):
            self.cloud.respond({'auth_session_id': transcript['auth_session_id'], 'proof': self.providers['edge'].prove('edge-response', transcript)})

    def test_ready_and_pending_identity_do_not_allow_business(self):
        answer = self.start()
        transcript = answer['transcript']
        self.cloud.respond({'auth_session_id': transcript['auth_session_id'], 'proof': self.providers['edge'].prove('edge-response', transcript)})
        self.edge.sessions[transcript['auth_session_id']] = transcript
        self.edge.active['aes0'] = transcript['auth_session_id']
        with self.assertRaises(ProtocolError):
            self.app.exchange(self.client.codec.seal('task.claim', {}, epoch='aes0'))

    def test_wrong_cloud_key_role_and_transcript_binding_rejected(self):
        answer = self.start()
        self.providers['edge'].peer = Ed25519PrivateKey.generate().public_key()
        with self.assertRaises(ProtocolError):
            self.providers['edge'].verify('cloud-challenge', answer['transcript'], answer['proof'])
        with self.assertRaises(ProtocolError):
            self.providers['cloud'].prove('edge-response', answer['transcript'])
        for field in ('device_id', 'key_id', 'cloud_id', 'epoch', 'credential_version', 'client_nonce'):
            transcript = {**answer['transcript'], field: 'other'}
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                self.providers['edge'].verify('cloud-challenge', transcript, answer['proof'])

    def test_expiry_renewal_and_revocation(self):
        self.client.exchange('heartbeat', {})
        old = self.edge.active['aes0']
        self.now += 875
        self.client.exchange('heartbeat', {})
        self.assertEqual(old, self.edge.active['aes0'])
        self.now += 25
        with self.assertRaises(ProtocolError):
            self.client.exchange('heartbeat', {})
        self.now -= 25
        self.cloud.revoke(self.edge.active['aes0'])
        with self.assertRaises(ProtocolError):
            self.client.exchange('heartbeat', {})
        self.providers['edge'].config['revoked'] = True
        self.edge.active.clear()
        with self.assertRaises(ProtocolError):
            self.client.exchange('heartbeat', {})

    def test_restart_invalidates_pending_but_preserves_active(self):
        self.client.exchange('heartbeat', {})
        active = self.edge.active['aes0']
        pending = self.start()['transcript']['auth_session_id']
        restarted = CloudAuth(self.path / 'auth.db', self.providers['cloud'], self.states['cloud'].codec,
                              cloud_id='control', clock=lambda: self.now)
        self.assertEqual(restarted.get(active)['epoch'], 'aes0')
        with self.assertRaises(ProtocolError):
            restarted.get(pending)

    def test_lost_activation_reply_reauthenticates_without_downgrade(self):
        self.drop = 'auth.activate'
        with self.assertRaises(TimeoutError):
            self.client.exchange('heartbeat', {})
        self.assertFalse(self.edge.active)
        self.assertTrue(self.client.exchange('heartbeat', {})['accepted'])

    def test_service_and_external_api_permissions(self):
        service = TestClient(auth_app(self.providers['cloud'], 't' * 32))
        self.assertEqual(service.get('/ready').status_code, 401)
        challenge = self.start()
        body = {'purpose': 'cloud-challenge', 'transcript': challenge['transcript'], 'envelope': None}
        self.assertEqual(service.post('/v1/prove', json=body).status_code, 401)
        self.assertEqual(service.post('/v1/prove', json=body, headers={'Authorization': 'Bearer ' + 't' * 32}).status_code, 200)
        self.assertEqual(service.post('/v1/prove', json={'message': 'arbitrary'}, headers={'Authorization': 'Bearer ' + 't' * 32}).status_code, 400)
        api = TestClient(create_app(self.app))
        self.assertEqual(api.post('/api/auth/revoke', json={'auth_session_id': 'x'}).status_code, 401)
        self.assertEqual(api.post('/api/edge/exchange', json=self.states['edge'].codec.seal('heartbeat', {}, epoch='aes0')).status_code, 400)

    def test_real_internal_auth_and_crypto_http_services(self):
        def serve(app):
            server = LiveServer(app)
            server.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.thread.join)
            self.addCleanup(server.shutdown)
            return 'http://127.0.0.1:' + str(server.server_port)
        token = 't' * 32
        (self.path / 'service.token').write_text(token)
        (self.path / 'root.key').write_bytes(bytes(range(32)))
        for role in ('cloud', 'edge'):
            provider = AuthServiceClient(serve(auth_app(self.providers[role], token)), token)
            services = {}
            for suite, name in (('AES-256-GCM', 'aes'), ('SM4-GCM', 'sm4')):
                config = {'suite': suite, 'device_id': 'jetson', 'key_id': 'key', 'role': role,
                          'key_file': str(self.path / 'root.key'), 'token_file': str(self.path / 'service.token'),
                          'state_path': str(self.path / (role + name + '.db')), 'initialize': True}
                services[suite] = serve(crypto_app(CryptoService(config, clock=lambda: self.now), token))
            codec = CryptoServiceCodec(device_id='jetson', key_id='key', role=role, services=services, token=token)
            codec.allow_epoch('aes0', 'AES-256-GCM')
            if role == 'cloud':
                self.cloud.codec = codec
                self.cloud.provider = provider
                self.app.codec.inner = codec
                self.app.codec.provider = provider
            else:
                self.edge.codec = codec
                self.edge.provider = provider
                self.client.codec.inner = codec
                self.client.codec.provider = provider
        self.assertTrue(self.client.exchange('heartbeat', {})['accepted'])
        message = self.wire[-1]
        self.assertEqual(message['header']['v'], 2)
        self.assertIn('auth_session_id', message['header'])

    def test_expired_credential_challenge_and_service_failure(self):
        answer = self.start()
        self.now += 61
        with self.assertRaises(ProtocolError):
            self.cloud.respond({'auth_session_id': answer['transcript']['auth_session_id'], 'proof': 'bad'})
        self.providers['cloud'].config['not_after'] = self.now
        with self.assertRaises(ProtocolError):
            self.client.exchange('heartbeat', {})

    def test_service_unavailable_blocks_authenticated_business(self):
        self.client.exchange('heartbeat', {})
        def unavailable(*args):
            raise TimeoutError('service down')
        self.providers['cloud'].verify = unavailable
        with self.assertRaises(TimeoutError):
            self.client.exchange('task.claim', {})


if __name__ == '__main__':
    unittest.main()
