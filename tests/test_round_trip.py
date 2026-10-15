import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from cloud.cloud_api.missions import MissionStore
from shared.communication.envelope import ProtocolError
from shared.communication.mission import advance_route, risk_context


class RoundTripGeometryTests(unittest.TestCase):
    def setUp(self):
        self.a = {'lat': 0, 'lon': 0}
        self.c = {'lat': .002, 'lon': 0}
        self.b = {'lat': .004, 'lon': 0}
        self.spec = {'route': [self.a, self.c, self.b, self.c, self.a],
                     'risks': [{'id': 'C', 'center': self.c, 'radius_m': 50}]}

    def test_closed_route_does_not_arrive_at_initial_a(self):
        index = advance_route(self.spec['route'], self.a, 0)
        self.assertEqual(index, 1)
        self.assertFalse(risk_context(self.spec, self.a, index)['arrival'])
        for point in self.spec['route'][1:]:
            index = advance_route(self.spec['route'], point, index)
        self.assertEqual(index, 5)
        self.assertTrue(risk_context(self.spec, self.a, index)['arrival'])

    def test_second_c_approach_is_not_ignored(self):
        risk = risk_context(self.spec, self.b, 3)
        self.assertGreater(risk['entry_distance_m'], 100)
        self.assertLess(risk['entry_distance_m'], 200)
        self.assertFalse(risk['inside'])

    def test_return_detour_does_not_reuse_outbound_c(self):
        d = {'lat': .004, 'lon': .003}
        e = {'lat': 0, 'lon': .003}
        self.spec['route'] = [self.a, self.c, self.b, d, e, self.a]
        self.assertIsNone(risk_context(self.spec, self.b, 3)['entry_distance_m'])
        self.assertFalse(risk_context(self.spec, self.a, 1)['arrival'])

    def test_position_cannot_skip_unvisited_intermediate_points(self):
        self.assertEqual(advance_route(self.spec['route'], self.b, 1), 1)


class RoundTripCloudTests(unittest.TestCase):
    def test_baseline_receipt_is_independent_for_each_revision(self):
        target = {'auth_policy': 'SM2', 'suite': 'SM4-GCM', 'protection_profile': 'lightweight'}
        self.switch.state = SimpleNamespace(active=lambda: 'e1', profile=lambda _: 'lightweight',
            policy=lambda _: 'SM2', suite=lambda _: 'SM4-GCM')
        for revision in (1, 2):
            self.record.update(revision=revision, plan={'phases': [{'stage': 'transit', 'target': target}]})
            self.store.save(self.record)
            for _ in range(2):
                self.record = self.store.dispatch('mission.receipt', 'jetson', {
                    'mission_id':self.record['mission_id'], 'revision':revision,
                    'stage':'transit', 'request_id':None, 'workloads_reconciled':True}, {'epoch':'e1','auth_session_id':'s1'}, self.switch, self.auth)
        self.assertEqual([r['revision'] for r in self.record['receipts']], [1, 2])

    def test_cloud_rejects_phase_receipt_without_old_workload_deactivation(self):
        with self.assertRaisesRegex(ProtocolError, 'deactivation confirmation'):
            self.store.dispatch('mission.receipt', 'jetson', {
                'mission_id':self.record['mission_id'], 'revision':1, 'stage':'risk', 'request_id':'switch'},
                {'epoch':'e1','auth_session_id':'s1'}, self.switch, self.auth)

    def setUp(self):
        RoundTripGeometryTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000
        self.path = Path(self.temp.name) / 'missions.db'
        self.store = MissionStore(self.path, clock=lambda: self.now)
        self.spec.update(goal='经过 C 后返回 A', resources={
            'cpu_budget_millicores':1000, 'memory_budget_mib':1024, 'cruise_speed_mps':10}, alarms=[])
        self.record = self.store.submit('jetson', 'roundtrip', self.spec)
        self.switch = SimpleNamespace(state=SimpleNamespace(profile=lambda _: 'enhanced'))
        self.auth = SimpleNamespace(get=lambda _: {'protection_profile':'enhanced',
            'session_expires':2000, 'key_expires':2000})

    def observe(self, point):
        self.now += 1
        self.record = self.store.dispatch('mission.observe', 'jetson', {
            'mission_id':self.record['mission_id'], 'revision':1, 'position':point,
            'sampled_at':self.now}, {'epoch':'e1','auth_session_id':'s1'}, self.switch, self.auth)

    def test_cloud_rejects_downgrade_before_returning_through_c(self):
        for point in (self.a, self.c, self.b):
            self.observe(point)
        for _ in range(11):
            self.observe(self.b)
        with self.assertRaisesRegex(ProtocolError, 'upcoming risk'):
            self.store.guard_switch({'device_id':'jetson','protection_profile':'lightweight'})
        for point in (self.c, self.a):
            self.observe(point)
        for _ in range(11):
            self.observe(self.a)
        self.store.guard_switch({'device_id':'jetson','protection_profile':'lightweight'})

    def test_restart_keeps_ordered_progress_but_resets_exit_stability(self):
        for point in (self.a, self.c, self.b):
            self.observe(point)
        self.assertEqual(self.record['next_waypoint'], 3)
        self.assertIsNotNone(self.record['outside_since'])
        self.store = MissionStore(self.path, clock=lambda: self.now)
        saved = self.store.get(self.record['mission_id'], 'jetson')
        self.assertEqual(saved['next_waypoint'], 3)
        self.assertIsNone(saved['outside_since'])
        self.observe(self.c)
        self.assertEqual(self.record['next_waypoint'], 4)

    def test_cloud_window_uses_30_seconds_and_adapts_to_real_preparation(self):
        for point in (self.a, self.c, self.b):
            self.observe(point)
        for _ in range(11):
            self.observe(self.b)
        # 返程入口约172米，速度1米/秒，已在默认30秒窗口之外。
        self.record['spec']['resources']['cruise_speed_mps'] = 1
        self.store.save(self.record)
        self.store.guard_switch({'device_id':'jetson','protection_profile':'lightweight'})
        self.record['preparation_durations'] = [150]
        self.store.save(self.record)
        with self.assertRaisesRegex(ProtocolError, 'upcoming risk'):
            self.store.guard_switch({'device_id':'jetson','protection_profile':'lightweight'})
