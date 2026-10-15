import json
import tempfile
import unittest
from pathlib import Path

from cloud.cloud_api.api import CloudApplication
from cloud.cloud_api.tasks import TaskStore
from services.crypto_service.repository.crypto import CounterRepository
from shared.communication.envelope import EnvelopeCodec
from shared.communication.switch import CloudSwitch, SwitchState
from jetson.edge_agent.catalog import ServiceCatalog
from jetson.edge_agent.client import EdgeClient
from jetson.edge_agent.executor import ExecutionJournal, TaskExecutor
from jetson.edge_agent.switch import SwitchCoordinator


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.now = 1000
        def state(role):
            codec = EnvelopeCodec(device_id='edge', key_id='key', root_key=bytes(32), role=role,
                                  counters=CounterRepository(self.path / (role + '-crypto.db')), epochs={}, clock=lambda: self.now)
            return SwitchState(self.path / (role + '-switch.db'), codec, 'aes', 'AES-256-GCM', initialize=True, clock=lambda: self.now)
        edge, cloud = state('edge'), state('cloud')
        self.store = TaskStore(self.path / 'tasks.db')
        self.app = CloudApplication(self.store, cloud.codec, switch=CloudSwitch(cloud, lambda suite: True),
                                    login_verifier=lambda auth: auth == 'Bearer ' + 'x' * 32)
        self.client = EdgeClient('http://cloud.example/api/edge/exchange', edge.codec,
                                 lambda: self.coordinator.business_epoch(), transport=self.app.exchange)
        self.coordinator = SwitchCoordinator(edge, self.client, lambda suite: True)
        document = json.loads((Path(__file__).resolve().parents[1] / 'jetson/edge_agent/config/service-catalog.json').read_text())
        for entry in document['services']:
            entry['registry_verified'] = True
        self.catalog = ServiceCatalog(document)
        entry = self.catalog.get('sm4-gcm-v1')
        selection = {'selection_kind': 'combination', 'target': {'auth_policy': 'Ed25519', 'suite': entry['suite'], 'protection_profile': 'lightweight'}}
        class Runner:
            starts = 0
            crash = False
            result = None
            retired = []
            def authentication(inner, policy, *, exclusive=False):
                return {'auth_policy': policy, 'exclusive': True} if exclusive else {'auth_policy': policy, 'ready': True}
            def start(inner, task):
                inner.starts += 1
                inner.result = {'state': 'completed', 'selection': selection}
                if inner.crash:
                    raise TimeoutError('Pi reply lost')
                return inner.result
            def lookup(inner, task_id):
                return inner.result
            def retire(inner, service_id):
                if service_id == 'aes-gcm-v1':
                    self.assertEqual(self.coordinator.state.latest()['state'], 'confirmed')
                inner.retired.append(service_id)
                return {'service_id': service_id, 'stopped': True}
        self.runner = Runner()
        self.journal = ExecutionJournal(self.path / 'execution.db', initialize=True)
        self.executor = TaskExecutor(self.client, self.coordinator, self.catalog, self.runner, self.journal)
        self.task = self.store.submit('edge', 'change1', '将仿真巡检通信保护切换为 SM4')

    def complete(self, executor=None):
        executor = executor or self.executor
        result = executor.once()
        if result['state'] == 'verifying':
            self.now += 31
            result = executor.once()
        return result

    def test_old_workloads_stay_running_through_transition_and_restart(self):
        self.assertEqual(self.executor.once()['state'], 'verifying')
        self.assertEqual(self.runner.retired, [])
        deadline = self.coordinator.state.latest()['retire_at']
        resumed = TaskExecutor(self.client, self.coordinator, self.catalog, self.runner,
            ExecutionJournal(self.path / 'execution.db'))
        self.now = deadline - 1
        self.assertEqual(resumed.once()['state'], 'verifying')
        self.assertEqual(self.runner.retired, [])
        self.now = deadline
        self.assertEqual(resumed.once()['state'], 'succeeded')
        self.assertEqual(self.runner.retired, ['aes-gcm-v1'])
        self.assertEqual(self.runner.starts, 1)

    def test_task_end_to_end_requires_mcp_receipt_and_cloud_verification(self):
        result = self.complete()
        self.assertEqual(result['state'], 'succeeded')
        self.assertEqual(self.runner.starts, 1)
        self.assertEqual(self.runner.retired, ['aes-gcm-v1'])
        self.assertEqual(self.store.get(self.task['task_id'])['state'], 'succeeded')
        self.assertEqual(self.executor.once()['state'], 'idle')
        self.assertEqual(self.runner.starts, 1)

    def test_restart_looks_up_existing_pi_run_instead_of_reinvoking(self):
        self.runner.crash = True
        with self.assertRaises(TimeoutError):
            self.executor.once()
        self.assertTrue(self.client.exchange('heartbeat', {'trust_start': 'disabled'})['accepted'])
        resumed = TaskExecutor(self.client, self.coordinator, self.catalog, self.runner,
                               ExecutionJournal(self.path / 'execution.db'))
        self.assertEqual(self.complete(resumed)['state'], 'succeeded')
        self.assertEqual(self.runner.starts, 1)

    def test_claimed_task_without_local_evidence_is_not_reexecuted(self):
        self.store.claim('edge')
        self.assertEqual(self.executor.once()['state'], 'unknown')
        self.assertEqual(self.runner.starts, 0)

    def test_model_text_cannot_declare_success(self):
        self.runner.start = lambda task: {'state': 'completed', 'selection': {'verified': True, 'suite': 'SM4-GCM'}}
        self.assertEqual(self.executor.once()['state'], 'failed')
        self.assertEqual(self.coordinator.state.active(), 'aes')
        self.assertEqual(self.store.get(self.task['task_id'])['state'], 'failed')

    def test_aborted_switch_stops_candidate_and_keeps_aes(self):
        self.app.switch.ready = lambda suite: False
        self.assertEqual(self.complete()['state'], 'failed')
        self.assertEqual(self.runner.retired, ['sm4-gcm-v1'])
        self.assertEqual(self.coordinator.state.active(), 'aes')

    def test_unconfirmed_deactivation_cannot_complete_task(self):
        retire = self.runner.retire
        self.runner.retire = lambda service_id: {'service_id': service_id, 'stopped': False}
        self.assertEqual(self.executor.once()['state'], 'verifying')
        self.now += 31
        with self.assertRaisesRegex(Exception, 'deactivation was not confirmed'):
            self.executor.once()
        self.assertNotEqual(self.store.get(self.task['task_id'])['state'], 'succeeded')
        self.runner.retire = retire
        self.assertEqual(self.complete()['state'], 'succeeded')

    def test_authentication_candidate_is_started_before_switch_and_retired_after_confirmation(self):
        calls = []
        def authentication(policy, *, exclusive=False):
            calls.append((policy, exclusive))
            if exclusive:
                self.assertEqual(self.coordinator.state.latest()['state'], 'confirmed')
                return {'auth_policy': policy, 'exclusive': True}
            self.assertEqual(self.coordinator.state.active(), 'aes')
            return {'auth_policy': policy, 'ready': True}
        self.runner.authentication = authentication
        self.runner.start = lambda task: {'state': 'completed', 'selection': {
            'selection_kind': 'combination', 'target': {'protection_profile': 'lightweight', 'auth_policy': 'Ed25519', 'suite': 'SM4-GCM'}}}
        self.assertEqual(self.complete()['state'], 'succeeded')
        self.assertEqual(calls, [('Ed25519', False), ('Ed25519', True)])

    def test_aborted_switch_retain_current_authentication(self):
        calls = []
        self.runner.authentication = lambda policy, **options: calls.append((policy, options))
        self.app.switch.ready = lambda suite: False
        self.assertEqual(self.complete()['state'], 'failed')
        self.assertEqual(calls, [('Ed25519', {}), ('Ed25519', {'exclusive': True})])

    def test_authentication_cleanup_failure_prevents_success_and_can_resume(self):
        from shared.communication.envelope import ProtocolError
        def unavailable(*args, **options):
            raise ProtocolError('authentication shutdown not confirmed')
        self.runner.authentication = unavailable
        with self.assertRaises(ProtocolError):
            self.executor.once()
        self.assertNotEqual(self.store.get(self.task['task_id'])['state'], 'succeeded')
        self.runner.authentication = lambda policy, **options: {'auth_policy': policy, 'exclusive': True}
        self.assertEqual(self.complete()['state'], 'succeeded')


if __name__ == '__main__':
    unittest.main()
