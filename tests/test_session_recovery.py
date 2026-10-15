"""跨真实认证、密码消息、持久切换层验证操作员恢复。"""
from fastapi.testclient import TestClient
import unittest
import test_auth as fixtures
from cloud.cloud_api.api import create_app
from shared.communication.envelope import ProtocolError


class SessionRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.AuthTests.setUp(self)
        self.client.auth_transport = lambda op, body: (self.app.recovery.offer(body) if op == 'recovery' else getattr(self.cloud, op)(body))

    def request_recovery(self):
        return self.app.recovery.request({'device_id': 'jetson', 'request_id': 'restore-one', 'expected_epoch': 'aes0'})

    def test_expired_recovery_requires_operator_then_real_receipt(self):
        self.client.exchange('heartbeat', {})
        expired = self.edge.for_epoch('aes0')['session_expires']
        self.now = expired + 1
        with self.assertRaises(ProtocolError):
            self.client.exchange('heartbeat', {})
        self.assertEqual(self.coordinator.recover_expired()['state'], 'paused')
        request = self.request_recovery()
        self.assertEqual(request['state'], 'waiting_device')
        self.assertEqual(self.request_recovery(), request)
        self.assertEqual(self.coordinator.recover_expired()['state'], 'succeeded')
        self.assertTrue(self.client.exchange('heartbeat', {})['accepted'])
        self.assertTrue(self.app.recovery.status()['business_verified'])
        self.assertNotEqual(self.states['edge'].active(), 'aes0')
        self.assertEqual(self.states['edge'].latest()['retire_at'], expired)
        self.assertEqual(self.states['cloud'].latest()['retire_at'], expired)
        self.assertEqual(self.cloud.deadline('aes0'), expired)
        with self.assertRaises(ProtocolError):
            self.client.exchange_at('heartbeat', {}, 'aes0')

    def test_recovery_authorization_and_valid_session_guard(self):
        api = TestClient(create_app(self.app))
        body = {'device_id': 'jetson', 'request_id': 'restore-one', 'expected_epoch': 'aes0'}
        self.assertEqual(api.post('/api/recovery', json=body).status_code, 401)
        self.client.exchange('heartbeat', {})
        self.assertEqual(api.post('/api/recovery', json=body, headers={'Authorization': 'Bearer '+'x'*32}).status_code, 409)

    def test_recovery_reconciles_lost_commit_without_duplicate_epoch(self):
        self.client.exchange('heartbeat', {})
        self.now = self.edge.for_epoch('aes0')['session_expires'] + 1
        self.request_recovery()
        self.drop = 'switch.commit'
        self.assertEqual(self.coordinator.recover_expired()['state'], 'unknown')
        candidate = self.states['cloud'].active()
        self.assertEqual(self.coordinator.recover_expired()['state'], 'succeeded')
        self.assertEqual(self.states['edge'].active(), candidate)

    def test_discovery_cannot_downgrade_or_skip_cloud_authentication(self):
        self.client.exchange('heartbeat', {})
        self.now = self.edge.for_epoch('aes0')['session_expires'] + 1
        self.request_recovery()
        binding = self.app.recovery.offer({'device_id':'jetson','old_epoch':'aes0','nonce':'a'*64})['binding'].copy()
        binding['suite'] = 'SM4-GCM'
        self.client.auth_transport = lambda op, body: {'binding': binding}
        with self.assertRaises(ProtocolError):
            self.coordinator.recover_expired()
        self.assertEqual(self.states['edge'].active(), 'aes0')

    def test_cloud_signed_recovery_resolves_edge_only_prepare(self):
        self.client.exchange('heartbeat', {})
        self.states['edge'].begin({'device_id':'jetson','old_epoch':'aes0','epoch':'orphan',
            'request_id':'orphan-request','suite':'AES-256-GCM','protection_profile':'lightweight'})
        self.now = self.edge.for_epoch('aes0')['session_expires'] + 1
        self.request_recovery()
        self.assertEqual(self.coordinator.recover_expired()['state'], 'succeeded')
        self.assertEqual(self.states['edge'].get('orphan-request')['state'], 'aborted')

    def test_timed_out_recovery_can_use_new_operator_request(self):
        self.client.exchange('heartbeat', {})
        self.now = self.edge.for_epoch('aes0')['session_expires'] + 1
        self.request_recovery()
        binding=self.states['cloud'].latest()['binding']
        self.states['edge'].begin(binding,timeout=120,grace=0,recovery=True)
        self.now += 121
        self.app.switch.maintain()
        self.app.recovery.request({'device_id':'jetson','request_id':'restore-two','expected_epoch':'aes0'})
        self.assertEqual(self.coordinator.recover_expired()['state'], 'succeeded')

    def test_unsigned_discovery_cannot_retire_orphan(self):
        self.client.exchange('heartbeat', {})
        self.states['edge'].begin({'device_id':'jetson','old_epoch':'aes0','epoch':'orphan',
            'request_id':'orphan-request','suite':'AES-256-GCM','protection_profile':'lightweight'})
        self.now = self.edge.for_epoch('aes0')['session_expires'] + 1
        self.request_recovery()
        transport=self.client.auth_transport
        def tampered(op, body):
            response=transport(op,body)
            if op=='recovery':response['proof']='invalid'
            return response
        self.client.auth_transport=tampered
        with self.assertRaises(ProtocolError):self.coordinator.recover_expired()
        self.assertEqual(self.states['edge'].get('orphan-request')['state'],'prepared')
