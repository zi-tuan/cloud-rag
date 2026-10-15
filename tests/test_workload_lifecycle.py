import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from jetson.edge_agent.lifecycle import WorkloadLifecycle
from shared.communication.envelope import ProtocolError


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.latest = {'state': 'confirmed', 'retire_at': 110}
        self.now = 100
        self.records = []
        self.state = SimpleNamespace(lock=threading.RLock(), latest=lambda: self.latest,
            clock=lambda: self.now, active=lambda: 'epoch', suite=lambda _: 'SM4-GCM',
            policy=lambda _: 'SM2', sync_codec=Mock(), get=Mock(side_effect=ProtocolError('unknown switch request')))
        self.runner = Mock()
        self.runner.authentication.return_value = {'auth_policy': 'SM2', 'exclusive': True}
        self.runner.retire.side_effect = lambda identifier: {'service_id': identifier, 'stopped': True}
        self.catalog = SimpleNamespace(for_suite=lambda _: {'id': 'sm4'},
            public=lambda: [{'id': 'aes'}, {'id': 'sm4'}])
        self.journal = SimpleNamespace(records=lambda: self.records)
        self.lifecycle = WorkloadLifecycle(self.state, self.runner, self.catalog, self.journal)

    def test_grace_then_stops_only_unused_workloads(self):
        self.assertEqual(self.lifecycle.once()['state'], 'waiting_transition')
        self.runner.retire.assert_not_called()
        self.now = 110
        self.assertEqual(self.lifecycle.once()['state'], 'reconciled')
        self.runner.authentication.assert_called_once_with('SM2', exclusive=True)
        self.runner.retire.assert_called_once_with('aes')

    def test_uncertain_or_unfinished_switch_never_stops(self):
        self.now = 200
        for latest in ({'state': 'prepared'}, {'state': 'confirmed', 'uncertain': True, 'retire_at': 110}):
            self.latest = latest
            self.assertEqual(self.lifecycle.once()['state'], 'waiting_transition')
        self.runner.retire.assert_not_called()

    def test_pending_mission_and_model_activation_are_reserved(self):
        self.now = 200
        for record in ({'pending': {'request_id': 'candidate'}}, {'stage': 'invoking'}, {'stage': 'selected'}, {'stage': 'switching'}):
            self.records = [record]
            self.assertEqual(self.lifecycle.once()['state'], 'waiting_preparation')
        self.runner.retire.assert_not_called()

    def test_old_mission_pending_record_with_proven_terminal_switch_does_not_block_cleanup(self):
        self.now = 200
        self.records = [{'pending': {'request_id': 'old-mission'}}]
        self.state.get.side_effect = None
        self.state.get.return_value = {'state': 'aborted', 'retire_at': 150}
        self.assertEqual(self.lifecycle.once()['state'], 'reconciled')

    def test_restart_reconciles_existing_terminal_transaction(self):
        self.now = 200
        self.records = [{'stage': 'completed'}, {'pending': None}]
        self.assertEqual(self.lifecycle.once()['state'], 'reconciled')
        fresh = WorkloadLifecycle(self.state, self.runner, self.catalog, self.journal)
        self.assertEqual(fresh.once()['state'], 'reconciled')

    def test_failed_stop_is_retried_and_not_cached_as_success(self):
        self.now = 200
        self.runner.retire.side_effect = [OSError('offline'), {'service_id': 'aes', 'stopped': True}]
        with self.assertRaises(OSError):
            self.lifecycle.once()
        self.assertEqual(self.lifecycle.once()['state'], 'reconciled')

    def test_repeated_checks_are_bounded_but_drift_is_rechecked(self):
        self.now = 200
        self.lifecycle.once()
        self.assertEqual(self.lifecycle.once()['state'], 'idle')
        self.now += 30
        self.assertEqual(self.lifecycle.once()['state'], 'reconciled')
