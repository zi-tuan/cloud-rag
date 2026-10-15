import tempfile
import unittest
import time
from pathlib import Path

from cloud.cloud_api.api import CloudApplication
from cloud.cloud_api.tasks import TaskStore
from services.crypto_service.repository.crypto import CounterRepository
from shared.communication.envelope import EnvelopeCodec, ProtocolError
from shared.communication.switch import CloudSwitch, SwitchState
from jetson.edge_agent.client import BusinessRejected, EdgeClient
from jetson.edge_agent.switch import SwitchCoordinator


class SwitchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.now = 1000
        self.cloud_ready = True
        self.edge_ready = True
        self.drop = None
        self.before = None
        self.wire = []
        self.restart(initialize=True)

    def restart(self, initialize=False):
        def state(role):
            codec = EnvelopeCodec(device_id='jetson', key_id='key', root_key=bytes(range(32)), role=role,
                                  counters=CounterRepository(self.path / (role + '-crypto.db')), epochs={}, clock=lambda: self.now)
            return SwitchState(self.path / (role + '-switch.db'), codec, 'aes0', 'AES-256-GCM',
                               initialize=initialize, clock=lambda: self.now)
        self.cloud_state, self.edge_state = state('cloud'), state('edge')
        self.cloud_switch = CloudSwitch(self.cloud_state, lambda suite: self.cloud_ready)
        self.app = CloudApplication(TaskStore(self.path / 'tasks.db'), self.cloud_state.codec,
                                    switch=self.cloud_switch,
                                    login_verifier=lambda auth: auth == 'Bearer ' + 'x' * 32)
        def transport(envelope):
            self.wire.append(envelope)
            if self.before:
                self.before(envelope)
            response = self.app.exchange(envelope)
            if self.drop == envelope['header']['kind']:
                self.drop = None
                raise TimeoutError('injected response loss')
            return response
        self.client = EdgeClient('http://cloud.example/api/edge/exchange', self.edge_state.codec,
                                 lambda: self.coordinator.business_epoch(), transport=transport)
        self.coordinator = SwitchCoordinator(self.edge_state, self.client, lambda suite: self.edge_ready)

    def begin_only(self):
        binding = {'protection_profile': 'lightweight', 'device_id': 'jetson', 'request_id': 'change1', 'old_epoch': 'aes0', 'epoch': 'sm4-1', 'suite': 'SM4-GCM'}
        record = self.edge_state.begin(binding)
        self.coordinator.call('switch.prepare', record, 'aes0')
        return record

    def test_delayed_confirmation_does_not_extend_cloud_transition_deadline(self):
        def delay(envelope):
            if envelope['header']['kind'] == 'switch.confirm':
                self.now += 7
        self.before = delay
        result = self.coordinator.start('change1', 'sm4-1', 'SM4-GCM')
        self.assertEqual(result['state'], 'succeeded')
        cloud_deadline = self.cloud_state.get('change1')['retire_at']
        self.assertEqual(self.edge_state.get('change1')['retire_at'], cloud_deadline)
        self.now = cloud_deadline
        self.edge_state.sync_codec()
        with self.assertRaises(ProtocolError):
            self.client.exchange_at('heartbeat', {}, 'aes0')

    def test_full_switch_real_crypto_and_business(self):
        self.assertTrue(self.client.exchange('heartbeat', {'trust_start': 'disabled'})['accepted'])
        result = self.coordinator.start('change1', 'sm4-1', 'SM4-GCM')
        self.assertEqual(result['state'], 'succeeded')
        self.assertEqual(self.cloud_state.active(), 'sm4-1')
        self.assertEqual(self.edge_state.active(), 'sm4-1')
        self.assertTrue(self.client.exchange('telemetry', {'position': [1, 2], 'sampled_at': time.time()})['accepted'])
        record = self.cloud_state.get('change1')
        self.assertIsNotNone(record['confirmed_receipt'])
        self.assertEqual({x['header']['suite'] for x in self.wire}, {'AES-256-GCM', 'SM4-GCM'})
        self.assertTrue(all(set(x) == {'header', 'ciphertext', 'tag'} for x in self.wire))
        self.assertEqual(self.coordinator.start('change1', 'sm4-1', 'SM4-GCM'), result)

    def test_lost_prepare_probe_commit_and_confirm_ack_reconcile_after_restart(self):
        # 每个测试子场景使用独立临时目录，避免复用 epoch 或密钥计数器。
        for kind in ('switch.prepare', 'switch.probe', 'switch.commit', 'switch.confirm'):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as name:
                    self.path = Path(name)
                    self.restart(initialize=True)
                    self.drop = kind
                    first = self.coordinator.start('change1', 'sm4-1', 'SM4-GCM')
                    self.assertEqual(first['state'], 'unknown')
                    with self.assertRaises(ProtocolError):
                        self.client.exchange('heartbeat', {})
                    self.restart()
                    final = self.coordinator.resume('change1')
                    self.assertEqual(final['state'], 'succeeded')
                    self.assertEqual(self.edge_state.active(), self.cloud_state.active())

    def test_not_ready_preserves_old_and_duplicate_does_not_retry(self):
        self.cloud_ready = False
        result = self.coordinator.start('change1', 'sm4-1', 'SM4-GCM')
        self.assertEqual(result['state'], 'failed')
        self.cloud_ready = True
        self.assertEqual(self.coordinator.resume('change1')['state'], 'failed')
        self.assertEqual(self.cloud_state.active(), 'aes0')
        self.assertTrue(self.client.exchange('heartbeat', {})['accepted'])

    def test_denied_prepare_keeps_old_business_and_allows_next_rotation(self):
        def denied(_): raise ProtocolError('downgrade requires stable risk exit')
        self.cloud_switch.authorize_binding = denied
        result = self.coordinator.start('exit-denied', 'exit-candidate', 'SM4-GCM')
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(self.edge_state.latest().get('uncertain'))
        self.assertTrue(self.client.exchange('heartbeat', {})['accepted'])
        self.cloud_switch.authorize_binding = lambda _: None
        self.assertEqual(self.coordinator.resume('exit-denied')['state'], 'failed')
        self.assertEqual(self.coordinator.start('rotate-next', 'rotate-next-epoch', 'AES-256-GCM')['state'], 'succeeded')

    def test_denied_commit_is_confirmed_abort_not_unknown(self):
        def changed(envelope):
            if envelope['header']['kind'] == 'switch.commit':
                def denied(_): raise ProtocolError('telemetry stale during preparation')
                self.cloud_switch.authorize_binding = denied
        self.before = changed
        result = self.coordinator.start('exit-raced', 'exit-raced-epoch', 'SM4-GCM')
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(self.edge_state.latest().get('uncertain'))
        self.assertTrue(self.client.exchange('heartbeat', {})['accepted'])

    def test_prepare_timeout_late_probe_and_commit_rejected(self):
        record = self.begin_only()
        self.now += 31
        for kind, epoch in [('switch.probe', 'sm4-1'), ('switch.commit', 'aes0')]:
            with self.assertRaises(BusinessRejected):
                self.coordinator.call(kind, record, epoch, edge_nonce='a' * 64, cloud_nonce='b' * 64)
        self.assertEqual(self.coordinator.resume('change1')['state'], 'failed')
        self.assertEqual(self.cloud_state.active(), 'aes0')

    def test_commit_ack_loss_must_reconcile_without_resurrecting_old_epoch(self):
        self.drop = 'switch.commit'
        self.coordinator.start('change1', 'sm4-1', 'SM4-GCM')
        self.assertEqual(self.cloud_state.active(), 'sm4-1')
        # 已提交的密钥退役不可撤销；取消不能重新启用旧 epoch。
        self.assertEqual(self.coordinator.abort('change1')['state'], 'unknown')
        self.assertEqual(self.cloud_state.active(), 'sm4-1')
        self.assertEqual(self.coordinator.resume('change1')['state'], 'succeeded')
        self.assertEqual(self.edge_state.active(), 'sm4-1')
        self.assertTrue(self.client.exchange('heartbeat', {})['accepted'])

    def test_disconnection_then_reconcile_no_blind_rollback(self):
        self.drop = 'switch.commit'
        self.coordinator.start('change1', 'sm4-1', 'SM4-GCM')
        def offline(_):
            raise TimeoutError('offline')
        self.before = offline
        self.assertEqual(self.coordinator.resume('change1')['state'], 'unknown')
        self.assertEqual(self.cloud_state.active(), 'sm4-1')
        self.before = None
        # 旧会话过期后仍能用候选会话对账并恢复，不要求回退明文。
        self.now += 200
        self.restart()
        self.assertEqual(self.coordinator.resume('change1')['state'], 'succeeded')

    def test_binding_conflict_wrong_epoch_and_fake_confirmation_rejected(self):
        record = self.begin_only()
        for change in ({'device_id': 'other'}, {'epoch': 'other'}, {'suite': 'AES-256-GCM'}):
            bad = {**record, 'binding': {**record['binding'], **change}}
            with self.assertRaises(BusinessRejected):
                self.coordinator.call('switch.status', bad, 'aes0')
        with self.assertRaises(BusinessRejected):
            self.coordinator.call('switch.probe', record, 'aes0', edge_nonce='a' * 64)
        with self.assertRaises(BusinessRejected):
            self.coordinator.call('switch.confirm', record, 'sm4-1', business_receipt='fake')
        with self.assertRaises(BusinessRejected):
            self.client.exchange_at('heartbeat', {}, 'sm4-1')

    def test_old_epoch_retires_and_duplicate_terminal_does_not_extend_grace(self):
        self.coordinator.start('change1', 'sm4-1', 'SM4-GCM')
        retire = self.edge_state.get('change1')['retire_at']
        message = self.edge_state.codec.seal('heartbeat', {}, epoch='aes0')
        self.now += 121
        self.assertEqual(self.coordinator.resume('change1')['state'], 'succeeded')
        self.assertEqual(self.edge_state.get('change1')['retire_at'], retire)
        # 云端维护后旧会话不能再解密，即使来包具有有效旧密钥认证标签。
        with self.assertRaises(ProtocolError):
            self.edge_state.codec.seal('heartbeat', {}, epoch='aes0')
        with self.assertRaises(ProtocolError):
            self.app.exchange(message)
        with self.assertRaises(ProtocolError):
            self.coordinator.start('change2', 'aes0', 'AES-256-GCM')

    def test_task_success_requires_server_evidence_and_matching_request(self):
        task = self.app.store.submit('jetson', 'change1', '切换国密')
        self.client.exchange('task.claim', {})
        self.client.exchange('task.event', {'task_id': task['task_id'], 'event_id': 'v1', 'state': 'verifying'})
        with self.assertRaises(BusinessRejected):
            self.client.exchange('task.complete', {'task_id': task['task_id'], 'verified': True})
        self.coordinator.start('change1', 'sm4-1', 'SM4-GCM')
        result = self.client.exchange('task.complete', {'task_id': task['task_id']})
        self.assertEqual(result['state'], 'succeeded')
        self.assertEqual(result['result']['suite'], 'SM4-GCM')
        self.assertEqual(self.client.exchange('task.complete', {'task_id': task['task_id']}), result)
        task2 = self.app.store.submit('jetson', 'other', '另一个任务')
        self.client.exchange('task.claim', {})
        self.client.exchange('task.event', {'task_id': task2['task_id'], 'event_id': 'v2', 'state': 'verifying'})
        with self.assertRaises(BusinessRejected):
            self.client.exchange('task.complete', {'task_id': task2['task_id']})


if __name__ == '__main__':
    unittest.main()
