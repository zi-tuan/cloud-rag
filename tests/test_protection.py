"""真实密码协议的分级期限、轮换、持久配额与退出守卫回归。"""
import copy
import tempfile
import unittest
import threading
from pathlib import Path
import test_auth
from shared.communication.envelope import ProtocolError
from shared.communication.mission import MissionSpec, risk_context
from cloud.cloud_api.quota import DeviceQuota, QuotaExceeded
from jetson.edge_agent.rotation import EpochRotation
from jetson.edge_agent.mission import MissionController
from jetson.edge_agent.executor import ExecutionJournal
from jetson.edge_agent.catalog import ServiceCatalog


class ProtectionTests(unittest.TestCase):
    start = test_auth.AuthTests.start
    def setUp(self):
        test_auth.AuthTests.setUp(self)
        self.cloud.profile_for_epoch = self.states['cloud'].profile
        self.edge.profile_for_epoch = self.states['edge'].profile

    def test_enhanced_has_fixed_120_second_session_and_epoch_key(self):
        self.client.exchange('heartbeat', {})
        result = self.coordinator.start('enhance', 'enhanced-1', 'AES-256-GCM', protection_profile='enhanced')
        self.assertEqual(result['state'], 'succeeded')
        session = self.edge.for_epoch('enhanced-1')
        self.assertEqual(session['protection_profile'], 'enhanced')
        self.assertEqual(session['session_expires'] - self.now, 120)
        self.assertEqual(session['key_expires'], session['session_expires'])
        self.now += 120
        with self.assertRaises(ProtocolError):
            self.client.exchange('heartbeat', {})

    def test_rotation_creates_new_epoch_before_expiry(self):
        self.client.exchange('heartbeat', {})
        old = self.states['edge'].active()
        self.now += 875
        result = EpochRotation(self.coordinator, self.edge).once()
        self.assertEqual(result['state'], 'succeeded')
        self.assertNotEqual(old, self.states['edge'].active())
        self.assertEqual(self.edge.for_epoch(self.states['edge'].active())['session_expires'] - self.now, 900)
        edge_record = self.states['edge'].latest()
        self.assertEqual(edge_record['retire_at'], self.edge.for_epoch(old)['session_expires'])
        self.assertEqual(edge_record['retire_at'], self.states['cloud'].latest()['retire_at'])
        self.now += 25
        with self.assertRaises(ProtocolError):
            self.client.exchange_at('heartbeat', {}, old)

    def test_rotation_starts_at_30_seconds_and_not_earlier(self):
        self.client.exchange('heartbeat', {})
        old = self.states['edge'].active()
        expires = self.edge.for_epoch(old)['key_expires']
        rotation = EpochRotation(self.coordinator, self.edge)
        self.now = expires - 31
        self.assertEqual(rotation.once()['state'], 'idle')
        self.assertEqual(self.states['edge'].active(), old)
        self.now += 1
        self.assertEqual(rotation.once()['state'], 'succeeded')
        self.assertNotEqual(self.states['edge'].active(), old)

    def test_reauthentication_cannot_extend_old_business_key(self):
        self.client.exchange('heartbeat', {})
        original = self.edge.for_epoch('aes0')['key_expires']
        self.now += 400
        self.edge.active.clear()
        self.client.exchange('heartbeat', {})
        self.assertEqual(self.edge.for_epoch('aes0')['session_expires'], original)
        self.now = original
        self.edge.active.clear()
        with self.assertRaises(ProtocolError):
            self.client.exchange('heartbeat', {})

    def test_authentication_count_is_durable_and_idempotent(self):
        self.client.exchange('heartbeat', {})
        session = self.edge.for_epoch('aes0')
        with self.cloud.db.connect() as db:
            receipt = db.execute('SELECT receipt FROM auth_sessions WHERE id=?', (session['auth_session_id'],)).fetchone()[0]
        self.client.exchange('auth.activate', {'receipt': receipt})
        with self.cloud.db.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM authentication_events').fetchone()[0], 1)
        self.now += 20
        self.edge.active.clear()
        self.client.exchange('heartbeat', {})
        with self.cloud.db.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM authentication_events').fetchone()[0], 2)
            db.execute('DELETE FROM auth_sessions')
        with self.cloud.db.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM authentication_events').fetchone()[0], 2)

    def test_rotation_maintains_retirement_while_no_rotation_is_due(self):
        self.client.exchange('heartbeat', {})
        old = self.states['edge'].active()
        self.coordinator.start('enhance-cleanup', 'enhanced-cleanup', 'AES-256-GCM', protection_profile='enhanced')
        self.states['edge'].codec.seal('heartbeat', {}, epoch=old)
        self.now += 31
        self.assertEqual(EpochRotation(self.coordinator, self.edge).once()['state'], 'idle')
        with self.assertRaises(ProtocolError):
            self.states['edge'].codec.seal('heartbeat', {}, epoch=old)

    def test_rotation_that_cannot_finish_before_expiry_requires_manual_recovery(self):
        self.client.exchange('heartbeat', {})
        old = self.states['edge'].active()
        expires = self.edge.for_epoch(old)['session_expires']
        transport, auth_transport = self.client.transport, self.client.auth_transport

        def slow_business(message):
            self.now += 10
            return transport(message)

        def slow_auth(operation, body):
            self.now += 10
            return auth_transport(operation, body)

        self.client.transport, self.client.auth_transport = slow_business, slow_auth
        self.now = expires - 30
        rotation = EpochRotation(self.coordinator, self.edge)
        self.assertNotEqual(rotation.once()['state'], 'succeeded')
        self.now = max(self.now, expires + 1)
        self.assertEqual(rotation.once(), {'state': 'paused', 'reason': 'session_expired_manual_recovery_required'})
        with self.assertRaises(ProtocolError):
            self.client.exchange('heartbeat', {})
        self.assertEqual(self.states['edge'].active(), old)

    def test_background_heartbeat_cannot_expire_prepared_rotation(self):
        self.client.exchange('heartbeat', {})
        old = self.states['edge'].active()
        self.now += 875
        transport = self.client.transport
        entered, finished = threading.Event(), threading.Event()
        outcome = {}

        def delayed_business(message):
            if message['header']['kind'] == 'heartbeat' and message['header']['epoch'] == old:
                self.now += 31
            return transport(message)

        def heartbeat():
            entered.set()
            try:
                outcome['heartbeat'] = self.client.exchange('heartbeat', {})
            except Exception as error:
                outcome['error'] = error
            finally:
                finished.set()

        worker = threading.Thread(target=heartbeat)
        readiness_checks = []

        def ready(suite):
            readiness_checks.append(suite)
            if len(readiness_checks) == 2:
                worker.start()
                self.assertTrue(entered.wait(1))
                finished.wait(.1)
            return True

        self.client.transport = delayed_business
        self.coordinator.ready = ready
        result = EpochRotation(self.coordinator, self.edge).once()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result['state'], 'succeeded')
        self.assertNotIn('error', outcome)
        self.assertTrue(outcome['heartbeat']['accepted'])

    def test_profile_signed_and_unregistered_fields_rejected(self):
        answer = self.start()
        transcript = copy.deepcopy(answer['transcript'])
        transcript['protection_profile'] = 'enhanced'
        with self.assertRaises(ProtocolError):
            self.providers['edge'].verify('cloud-challenge', transcript, answer['proof'])
        body = {k: answer['transcript'][k] for k in ('device_id','policy','credential_version','key_id','epoch','suite','client_nonce','request_id')}
        with self.assertRaises(ProtocolError):
            self.cloud.start(body)

    def test_device_quota_survives_new_epoch(self):
        self.client.exchange('heartbeat', {})
        quota = self.app.quota
        with quota.db.connect() as db:
            db.execute('INSERT INTO quota_requests SELECT ?,?,? FROM quota_requests LIMIT 1', ('jetson', 'business', self.now))
            count = db.execute("SELECT count(*) FROM quota_requests WHERE lane='business'").fetchone()[0]
            db.executemany('INSERT INTO quota_requests VALUES (?,?,?)', [('jetson','business',self.now)] * (180 - count))
        result = self.coordinator.start('enhance-quota', 'enhance-quota-1', 'AES-256-GCM', protection_profile='enhanced')
        # 新会话首条真实业务也受旧会话累计配额限制，不能以切换重置。
        self.assertEqual(result['state'], 'unknown')
        with self.assertRaises(ProtocolError):
            self.client.exchange_at('telemetry', {}, 'enhance-quota-1')

    def controller(self, risk_suite='AES-256-GCM'):
        spec = MissionGeometryTests().spec()
        mission = self.app.missions.submit('jetson', 'route-mission', spec)
        plan = {'rationale':'沿参考航线提前增强，稳定退出后恢复轻量', 'phases':[
            {'stage':stage,'target':{'auth_policy':'Ed25519','suite':risk_suite if stage == 'risk' else 'AES-256-GCM','protection_profile':profile},'reason':'依据风险与登记服务'}
            for stage,profile in [('transit','lightweight'),('risk','enhanced'),('exit','lightweight')]]}
        class Runner:
            calls = 0
            workloads = {'aes-gcm-v1': True, 'sm4-gcm-v1': False}
            def start(inner, task):
                inner.calls += 1
                return {'state':'completed','selection':{'selection_kind':'mission_plan','plan':plan},'model_reply':'已规划'}
            def activate(inner, service):
                inner.workloads[service] = True
                return {'ready':True}
            def authentication(inner, policy, exclusive=False):
                return {'auth_policy': policy, 'exclusive': True} if exclusive else {'ready':True}
            def retire(inner, service):
                inner.workloads[service] = False
                return {'service_id': service, 'stopped': True}
        self.sample = {'position':spec['route'][0], 'sampled_at':self.now, 'speed_mps':10}
        self.runner = Runner()
        controller = MissionController(self.client,self.coordinator,self.runner,
            ServiceCatalog.load('jetson/edge_agent/config/service-catalog.json'), ExecutionJournal(self.path/'missions.db',initialize=True),
            lambda:self.sample,resources=lambda:{'available':True,'cpu_millicores':100,'memory_mib':128},
            clock=lambda:self.now,monotonic=lambda:self.now)
        return controller,mission

    def tick(self, controller, lon, seconds=1):
        self.now += seconds
        self.sample = {'position':{'lat':34,'lon':lon},'sampled_at':self.now,'speed_mps':10}
        return controller.once()

    def complete_switch(self, controller, lon):
        result = self.tick(controller, lon)
        for _ in range(15):
            if result['state'] != 'waiting_retirement':
                return result
            result = self.tick(controller, lon, seconds=3)
        self.fail('old workload retirement did not finish')

    def test_route_does_not_release_risk_gate_or_report_success_before_old_pods_stop(self):
        controller, mission = self.controller(risk_suite='SM4-GCM')
        self.assertEqual(controller.once()['state'], 'monitoring')
        self.assertEqual(self.tick(controller, 108.047)['state'], 'preparing')
        result = self.tick(controller, 108.047)
        self.assertEqual(self.states['edge'].latest()['state'], 'confirmed')
        self.assertEqual(result['state'], 'waiting_retirement')
        self.assertIsNone(controller.flight_gate_evidence)
        self.assertTrue(all(self.runner.workloads.values()))
        actual = self.app.missions.get(mission['mission_id'], 'jetson')
        self.assertNotIn('risk', [receipt['stage'] for receipt in actual['receipts']])
        self.assertEqual(self.complete_switch(controller, 108.047)['state'], 'succeeded')
        self.assertTrue(controller.flight_gate_evidence['workloads_reconciled'])
        actual = self.app.missions.get(mission['mission_id'], 'jetson')
        self.assertTrue(actual['receipts'][-1]['workloads_reconciled'])
        self.assertEqual(self.runner.workloads, {'aes-gcm-v1': False, 'sm4-gcm-v1': True})

    def test_failed_old_pod_stop_blocks_risk_receipt_and_retries_after_restart(self):
        controller, mission = self.controller(risk_suite='SM4-GCM')
        controller.once()
        self.tick(controller, 108.047)
        self.tick(controller, 108.047)
        original = self.runner.retire
        self.runner.retire = lambda service: {'service_id': service, 'stopped': False}
        with self.assertRaisesRegex(ProtocolError, 'deactivation'):
            self.tick(controller, 108.047, seconds=30)
        self.assertIsNone(controller.flight_gate_evidence)
        self.assertTrue(all(self.runner.workloads.values()))
        self.assertNotIn('risk', [r['stage'] for r in self.app.missions.get(mission['mission_id'], 'jetson')['receipts']])
        self.runner.retire = original
        restarted = MissionController(self.client, self.coordinator, self.runner, controller.catalog,
            controller.journal, lambda: self.sample, resources=controller.resources,
            clock=lambda: self.now, monotonic=lambda: self.now)
        self.assertEqual(restarted.once()['state'], 'succeeded')
        self.assertTrue(restarted.flight_gate_evidence['workloads_reconciled'])
        self.assertEqual(self.runner.workloads, {'aes-gcm-v1': False, 'sm4-gcm-v1': True})
        self.assertEqual(self.runner.calls, 1)

    def test_rotation_during_old_pod_stop_is_not_blocked_and_receipt_uses_current_epoch(self):
        controller, mission = self.controller(risk_suite='SM4-GCM')
        controller.once()
        self.tick(controller, 108.047)
        self.tick(controller, 108.047)
        original = self.runner.retire
        finished, outcomes = threading.Event(), []
        def rotate():
            try:
                outcomes.append(EpochRotation(self.coordinator, self.edge).once())
            finally:
                finished.set()
        worker = threading.Thread(target=rotate)
        def stop(service):
            self.now = self.edge.for_epoch(self.states['edge'].active())['session_expires'] - 30
            worker.start()
            self.assertTrue(finished.wait(2), 'old Pod stop blocked mandatory session rotation')
            return original(service)
        self.runner.retire = stop
        try:
            self.assertEqual(self.tick(controller, 108.047, seconds=30)['state'], 'succeeded')
        finally:
            if worker.ident is not None:
                worker.join(5)
        self.assertEqual(outcomes[0]['state'], 'succeeded')
        actual = self.app.missions.get(mission['mission_id'], 'jetson')['receipts'][-1]
        self.assertEqual(actual['epoch'], self.states['edge'].active())
        self.assertTrue(actual['request_id'].startswith('rotate-'))
        self.assertTrue(actual['workloads_reconciled'])

    def test_risk_window_is_30_seconds_and_counts_service_preparation(self):
        controller, mission = self.controller()
        controller.once()
        # 距入口约 2666 米，ETA 266 秒：旧 300 秒窗口会错误地提前增强。
        result = self.tick(controller, 108.02)
        self.assertEqual(result['state'], 'monitoring')
        self.assertEqual(result['lead_seconds'], 30)
        original = self.runner.activate
        def slow_activation(service):
            self.now += 12
            return original(service)
        self.runner.activate = slow_activation
        self.assertEqual(self.tick(controller, 108.047)['state'], 'preparing')
        self.assertEqual(self.complete_switch(controller, 108.047)['state'], 'succeeded')
        record = controller.journal.get('mission-' + mission['mission_id'])
        self.assertGreaterEqual(record['durations'][-1], 13)
        result = self.tick(controller, 108.047)
        self.assertGreaterEqual(result['lead_seconds'], 43)

    def test_route_loop_has_cloud_business_receipts_and_one_model_plan(self):
        controller,mission = self.controller()
        self.assertEqual(controller.once()['state'],'monitoring')
        self.assertEqual(self.tick(controller,108.047)['state'],'preparing')
        self.assertEqual(self.complete_switch(controller,108.047)['state'],'succeeded')
        self.assertEqual(self.states['edge'].profile(self.states['edge'].active()),'enhanced')
        self.tick(controller,108.05,seconds=30)
        self.tick(controller,108.055)
        self.tick(controller,108.06,seconds=5)
        self.assertEqual(self.states['edge'].profile(self.states['edge'].active()),'enhanced')
        self.assertEqual(self.tick(controller,108.1,seconds=5)['state'],'preparing')
        self.assertEqual(self.complete_switch(controller,108.1)['state'],'succeeded')
        self.tick(controller,108.1)
        actual = self.app.missions.get(mission['mission_id'],'jetson')
        self.assertEqual(actual['state'],'succeeded')
        self.assertEqual([r['protection_profile'] for r in actual['receipts']],['lightweight','enhanced','lightweight'])
        self.assertTrue(all(r['new_session_business_verified'] for r in actual['receipts']))
        self.assertEqual(self.runner.calls,1)

    def test_inherited_enhanced_waits_for_stable_fresh_exit_before_transit(self):
        self.client.exchange('heartbeat', {})
        self.assertEqual(self.coordinator.start('inherited', 'inherited-epoch', 'AES-256-GCM',
            protection_profile='enhanced')['state'], 'succeeded')
        controller, _ = self.controller()
        controller.once()
        self.assertEqual(self.states['edge'].profile(self.states['edge'].active()), 'enhanced')
        for _ in range(31):
            self.tick(controller, 108)
        self.assertEqual(self.states['edge'].profile(self.states['edge'].active()), 'lightweight')

    def test_cloud_observed_C_is_retained_when_control_loop_misses_inside_sample(self):
        controller, mission = self.controller()
        controller.once()
        self.tick(controller, 108.047)
        self.tick(controller, 108.047)
        self.now += 30
        # 独立遥测在控制线程繁忙时证明穿 C；控制器的下一采样已经在区外。
        self.client.exchange('mission.observe', {'mission_id': mission['mission_id'], 'revision': 1,
            'position': {'lat': 34, 'lon': 108.05}, 'sampled_at': self.now})
        self.tick(controller, 108.055)
        self.assertEqual(controller.journal.get('mission-' + mission['mission_id'])['visited'], ['C'])
        for _ in range(50):
            self.tick(controller, 108.1)
        actual = self.app.missions.get(mission['mission_id'], 'jetson')
        self.assertIn('exit', [receipt['stage'] for receipt in actual['receipts']])
        self.assertEqual(actual['state'], 'succeeded')

    def test_restart_after_lightweight_exit_waits_without_reentering_enhanced(self):
        controller, _ = self.controller()
        controller.once()
        self.tick(controller, 108.047)
        self.tick(controller, 108.047)
        self.tick(controller, 108.05, seconds=30)
        for _ in range(13):
            self.tick(controller, 108.06)
        self.assertEqual(self.states['edge'].profile(self.states['edge'].active()), 'lightweight')
        restarted = MissionController(self.client, self.coordinator, self.runner, controller.catalog,
            controller.journal, lambda: self.sample, resources=controller.resources,
            clock=lambda: self.now, monotonic=lambda: self.now)
        self.assertEqual(restarted.once()['state'], 'waiting_exit_evidence')
        self.assertEqual(self.states['edge'].profile(self.states['edge'].active()), 'lightweight')

    def test_stale_telemetry_and_position_jitter_cannot_downgrade(self):
        controller,mission = self.controller()
        controller.once()
        self.tick(controller,108.047)
        self.tick(controller,108.047)
        self.tick(controller,108.05,seconds=30)
        self.tick(controller,108.055)
        self.now += 6
        self.assertEqual(controller.once()['state'],'waiting_telemetry')
        self.tick(controller,108.055)
        self.tick(controller,108.051,seconds=9)
        self.tick(controller,108.055)
        self.assertEqual(self.states['edge'].profile(self.states['edge'].active()),'enhanced')
        self.assertEqual(self.runner.calls,1)
        self.assertEqual(self.app.missions.get(mission['mission_id'],'jetson')['state'],'running')

    def test_live_telemetry_advances_exit_evidence_while_control_lock_is_held(self):
        from jetson.edge_agent.client import SessionTelemetryClient
        controller, mission = self.controller()
        controller.once()
        self.tick(controller, 108.047)
        self.tick(controller, 108.047)
        telemetry = SessionTelemetryClient(self.client.url, self.client.codec,
            self.states['edge'].active, transport=self.client.transport)
        errors = []
        done = threading.Event()

        def report():
            try:
                for lon in [108.05, 108.055, 108.06, 108.07, 108.08, 108.09]:
                    self.now += 3
                    telemetry.exchange('telemetry', {'online': True, 'sampled_at': self.now,
                        'messages': {'GLOBAL_POSITION_INT': {'age_seconds': 0,
                            'fields': {'lat': 340000000, 'lon': int(lon * 1e7), 'vx': 0, 'vy': 1000}}}})
            except Exception as error:
                errors.append(error)
            finally:
                done.set()

        with self.client.lock:
            worker = threading.Thread(target=report)
            worker.start()
            self.assertTrue(done.wait(5), 'control transaction blocked fresh telemetry')
        worker.join(5)
        self.assertEqual(errors, [])
        observed = self.app.missions.get(mission['mission_id'], 'jetson')
        self.assertEqual(observed['visited'], ['C'])
        self.assertEqual(observed['protection_timing']['speed_mps'], 10)
        self.assertGreaterEqual(self.now - observed['outside_since'], 10)
        self.app.missions.guard_switch({'device_id': 'jetson', 'protection_profile': 'lightweight'})
        self.now += 6
        with self.assertRaises(ProtocolError):
            self.app.missions.guard_switch({'device_id': 'jetson', 'protection_profile': 'lightweight'})
        self.now = self.edge.for_epoch(self.states['edge'].active())['session_expires']
        with self.assertRaises(ProtocolError):
            telemetry.exchange('telemetry', {})
        with self.assertRaises(ProtocolError):
            telemetry.exchange('switch.prepare', {})

    def test_denied_exit_retries_after_fresh_evidence_without_blocking_old_session(self):
        controller, mission = self.controller()
        controller.once()
        self.tick(controller, 108.047)
        self.tick(controller, 108.047)
        self.tick(controller, 108.05, seconds=30)
        for _ in range(3):
            result = self.tick(controller, 108.1, seconds=5)
        self.assertEqual(result['state'], 'preparing')
        guard = self.app.switch.authorize_binding
        def deny(_):
            raise ProtocolError('fresh observation temporarily unavailable')
        self.app.switch.authorize_binding = deny
        self.assertEqual(self.tick(controller, 108.1)['state'], 'failed')
        self.assertTrue(self.client.exchange('heartbeat', {})['accepted'])
        self.app.switch.authorize_binding = guard
        self.assertEqual(self.tick(controller, 108.1, seconds=5)['state'], 'waiting_retry')
        self.assertEqual(self.tick(controller, 108.1, seconds=5)['state'], 'preparing')
        self.assertEqual(self.complete_switch(controller, 108.1)['state'], 'succeeded')
        self.tick(controller, 108.1)
        self.assertEqual(self.app.missions.get(mission['mission_id'], 'jetson')['state'], 'succeeded')

    def test_slow_service_activation_does_not_block_due_epoch_rotation(self):
        controller, _ = self.controller()
        controller.once()
        self.now = self.edge.for_epoch(self.states['edge'].active())['session_expires'] - 30
        finished = threading.Event()
        outcome = []
        def rotate():
            try:
                outcome.append(EpochRotation(self.coordinator, self.edge).once())
            finally:
                finished.set()
        worker = threading.Thread(target=rotate)
        def activate(_):
            worker.start()
            self.assertTrue(finished.wait(2), 'service readiness wait blocked mandatory rotation')
            return {'ready': True}
        self.runner.activate = activate
        try:
            self.tick(controller, 108.047, seconds=0)
        finally:
            if worker.ident is not None:
                worker.join(5)
        self.assertEqual(outcome[0]['state'], 'succeeded')

    def test_missing_registered_capability_is_explicitly_unmet(self):
        controller, mission = self.controller()
        controller.catalog = ServiceCatalog({'version': 1, 'services': []})
        self.assertEqual(controller.once()['state'], 'unmet')
        self.assertEqual(self.app.missions.get(mission['mission_id'], 'jetson')['state'], 'unmet')

    def test_unknown_resources_do_not_mean_zero_usage(self):
        controller, mission = self.controller()
        controller.resources = lambda: {'available': False}
        self.assertEqual(controller.once()['state'], 'waiting_resources')
        self.assertEqual(self.runner.calls, 0)
        self.assertEqual(self.app.missions.get(mission['mission_id'], 'jetson')['reason'], 'resource_measurement_unavailable')


class MissionGeometryTests(unittest.TestCase):
    def spec(self):
        return MissionSpec.model_validate({'goal':'经过 C 后恢复轻量', 'route':[{'lat':34,'lon':108},{'lat':34,'lon':108.05},{'lat':34,'lon':108.10}],
            'risks':[{'id':'C','center':{'lat':34,'lon':108.05},'radius_m':100}]}).model_dump()

    def test_new_circle_route_and_exit_margin(self):
        spec = self.spec()
        context = risk_context(spec, spec['route'][0], 0)
        self.assertGreater(context['entry_distance_m'], 4000)
        self.assertEqual(risk_context(spec, spec['route'][1], 1)['inside'], ['C'])
        self.assertIsNone(risk_context(spec, spec['route'][2], 3)['entry_distance_m'])
        self.assertTrue(risk_context(spec, spec['route'][2], 3)['outside_margin'])

    def test_quota_persistent_rolling_window_and_independent_control(self):
        with tempfile.TemporaryDirectory() as root:
            now = [1000]
            path = Path(root)/'quota.db'
            quota = DeviceQuota(path, clock=lambda:now[0])
            for _ in range(3):
                quota.consume('jetson','business',3)
            with self.assertRaises(QuotaExceeded):
                DeviceQuota(path, clock=lambda:now[0]).consume('jetson','business',3)
            quota.consume('jetson','control',3)
            now[0] += 60
            quota.consume('jetson','business',3)


if __name__ == '__main__':
    unittest.main()
