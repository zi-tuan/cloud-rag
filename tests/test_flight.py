import tempfile
import unittest
import threading
from pathlib import Path
from types import SimpleNamespace
from shared.communication.flight import FlightRequest, validate_route
from shared.communication.envelope import ProtocolError
from cloud.cloud_api.flights import FlightStore
from jetson.edge_agent.executor import ExecutionJournal
from jetson.edge_agent.flight import FlightController
from shared.adapters.simulation_flight import SimulationFlight


def mission():
    a, c, b = {'lat': -35.363262, 'lon': 149.1652373}, {'lat': -35.359262, 'lon': 149.1652373}, {'lat': -35.355262, 'lon': 149.1652373}
    return {'mission_id': 'm1', 'revision': 1, 'state': 'running', 'plan': {'phases': []}, 'visited': [], 'violations': [],
            'receipts': [{'revision': 1, 'stage': 'transit', 'workloads_reconciled': True}], 'spec': {'goal': '飞经 C', 'route': [a, c, b], 'risks': [{'id': 'C', 'center': c, 'radius_m': 200}], 'alarms': [],
            'resources': {'cpu_budget_millicores': 1000, 'memory_budget_mib': 1024, 'cruise_speed_mps': 10}}}


class FlightTests(unittest.TestCase):
    def test_total_route_limit_is_100km_including_return(self):
        import math
        start = {'lat': 0, 'lon': 0}
        self.mission['spec']['risks'][0]['center'] = {'lat': 20, 'lon': 0}
        self.sample['position'] = start
        def endpoint(meters):
            return {'lat': 0, 'lon': math.degrees(meters / 6371000)}
        for meters in (6000, 99999):
            self.mission['spec']['route'] = [start, endpoint(meters)]
            validate_route(self.mission, self.sample, 'enhanced', self.now)
        for route in ([start, endpoint(100001)], [start, endpoint(60000), start]):
            self.mission['spec']['route'] = route
            with self.assertRaisesRegex(ProtocolError, '^flight_route_exceeds_100km$'):
                validate_route(self.mission, self.sample, 'enhanced', self.now)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000
        self.mission = mission()
        self.sample = {'position': self.mission['spec']['route'][0], 'sampled_at': self.now}
        self.store = FlightStore(Path(self.temp.name)/'cloud.db', lambda: self.now)
        telemetry = {'online': True, 'sampled_at': self.now, 'messages': {'GLOBAL_POSITION_INT': {'age_seconds': 0, 'fields': {'lat': round(self.sample['position']['lat']*1e7), 'lon': round(self.sample['position']['lon']*1e7)}}}}
        self.app = SimpleNamespace(codec=SimpleNamespace(device_id='jetson'), store=SimpleNamespace(device=lambda _: {'online': True, 'last_seen': self.now, 'status': {'telemetry': telemetry}}),
                                  switch=SimpleNamespace(state=SimpleNamespace(active=lambda: 'epoch1', profile=lambda _: 'enhanced')),
                                  auth=SimpleNamespace(status=lambda _: {'authenticated': True}), missions=SimpleNamespace(get=lambda *_: self.mission))
        self.body = {'device_id': 'jetson', 'request_id': 'req1', 'action': 'route', 'mission_id': 'm1', 'revision': 1}

    def test_reject_cross_continent_route_and_wait_for_enhanced(self):
        with self.assertRaisesRegex(ProtocolError, 'too_far'):
            validate_route(self.mission, {'position': {'lat': 34, 'lon': 108}, 'sampled_at': 1000}, 'enhanced', 1000)
        with self.assertRaisesRegex(ProtocolError, 'enhanced'):
            validate_route(self.mission, self.sample, 'lightweight', 1000)

    def test_stale_alarm_and_endpoint_guard(self):
        with self.assertRaisesRegex(ProtocolError, 'stale'):
            validate_route(self.mission, self.sample, 'enhanced', 1006)
        self.mission['spec']['alarms'] = ['network']
        with self.assertRaisesRegex(ProtocolError, 'alarm'):
            validate_route(self.mission, self.sample, 'enhanced', 1000)

        self.mission['spec']['alarms'] = []
        self.mission['spec']['route'][-1] = self.mission['spec']['route'][1]
        with self.assertRaisesRegex(ProtocolError, '50m'):
            validate_route(self.mission, self.sample, 'enhanced', 1000)

    def test_duplicate_request_survives_store_restart_and_no_reclaim(self):
        first = self.store.submit(self.body, self.app)
        reopened = FlightStore(Path(self.temp.name)/'cloud.db', lambda: self.now)
        self.assertEqual(reopened.submit(self.body, self.app)['flight_id'], first['flight_id'])
        self.store.dispatch('flight.claim', 'jetson', {'flight_id': first['flight_id']})
        with self.assertRaisesRegex(ProtocolError, 'no_reexecution'):
            reopened.dispatch('flight.claim', 'jetson', {'flight_id': first['flight_id']})

    def test_request_reuse_and_revision_guard(self):
        self.store.submit(self.body, self.app)
        with self.assertRaisesRegex(ProtocolError, 'conflict'):
            self.store.submit({**self.body, 'altitude_m': 25}, self.app)
        with self.assertRaisesRegex(ProtocolError, 'revision'):
            self.store.submit({**self.body, 'request_id': 'req2', 'revision': 2}, self.app)

    def test_operator_hold_preempts_route_and_expired_queue(self):
        route = self.store.submit(self.body, self.app)
        hold = self.store.submit({'device_id': 'jetson', 'request_id': 'req2', 'action': 'hold'}, self.app)
        self.assertEqual(self.store.records('jetson')[1]['state'], 'interrupted')
        self.assertEqual(self.store.dispatch('flight.poll', 'jetson', {})['flight_id'], hold['flight_id'])
        self.now += 61
        self.assertEqual(self.store.dispatch('flight.claim', 'jetson', {'flight_id': hold['flight_id']})['state'], 'failed')
        self.assertNotEqual(route['flight_id'], hold['flight_id'])

    def test_worker_restart_reconciles_without_flight(self):
        record = self.store.submit(self.body, self.app)
        self.store.dispatch('flight.claim', 'jetson', {'flight_id': record['flight_id']})
        client = SimpleNamespace(exchange=lambda kind, body: self.store.dispatch(kind, 'jetson', body))
        transport = SimpleNamespace(begin=lambda *_: self.fail('restart must not fly'), close=lambda: None)
        worker = FlightController(client, ExecutionJournal(Path(self.temp.name)/'edge.db', initialize=True), transport, lambda: self.sample, lambda: 'enhanced', clock=lambda: self.now)
        self.assertEqual(worker.once()['state'], 'interrupted')

    def test_worker_has_no_autonomous_flight_when_queue_empty(self):
        client = SimpleNamespace(exchange=lambda *_: None)
        transport = SimpleNamespace(begin=lambda *_: self.fail('no operator command'))
        worker = FlightController(client, ExecutionJournal(Path(self.temp.name)/'edge.db', initialize=True), transport, lambda: self.sample, lambda: 'enhanced', clock=lambda: self.now)
        self.assertEqual(worker.once()['state'], 'idle')

    def test_failed_upload_retains_ack_evidence_and_does_not_reexecute(self):
        record = self.store.submit(self.body, self.app)
        self.store.dispatch('flight.claim', 'jetson', {'flight_id': record['flight_id']})
        def rejected():
            raise ProtocolError('flight_mission_upload_rejected')
        evidence = {'mission_ack_type': 1, 'mission_requested_sequences': [0, 1], 'mission_upload_ack': False}
        transport = SimpleNamespace(tick=rejected, evidence=evidence, close=lambda: None)
        client = SimpleNamespace(exchange=lambda kind, body: self.store.dispatch(kind, 'jetson', body))
        journal = ExecutionJournal(Path(self.temp.name)/'edge.db', initialize=True)
        worker = FlightController(client, journal, transport, lambda: self.sample, lambda: 'enhanced', clock=lambda: self.now)
        worker.current, worker.last_poll = record, self.now
        worker.once()
        saved = journal.get('flight-' + record['flight_id'])
        self.assertEqual(saved['state'], 'failed')
        self.assertEqual(saved['evidence'], evidence)
        self.assertIsNone(worker.current)
        self.assertEqual(worker.once()['state'], 'idle')

    def test_strict_simulation_and_finite_velocity_contract(self):
        for endpoint in ('tcp:192.168.8.110:5763', 'tcp:other-host:5762'):
            with self.assertRaises(ValueError): SimulationFlight(endpoint)
        for vector in ([float('nan'), 0, 0], [3, 0, 0], [1, 2]):
            with self.assertRaises(ValueError): FlightRequest(device_id='jetson', request_id='r', action='velocity', velocity=vector)


class ProtocolTests(unittest.TestCase):
    def test_mavlink_keeps_running_while_cloud_report_is_blocked(self):
        from jetson.edge_agent.flight import ThreadedFlightTransport
        report_started, report_release, progress = threading.Event(), threading.Event(), threading.Event()
        calls = []
        class Transport:
            state = 'uploading'
            evidence = {}
            def begin(self, *_): pass
            def close(self): pass
            def tick(self):
                calls.append(1)
                if report_started.is_set(): progress.set()
                return {'state': 'running', 'evidence': {'received':len(calls)}}
        def exchange(kind, body):
            if kind == 'flight.event':
                report_started.set()
                report_release.wait(2)
            return body
        with tempfile.TemporaryDirectory() as directory:
            transport = ThreadedFlightTransport(Transport())
            worker = FlightController(SimpleNamespace(exchange=exchange), ExecutionJournal(Path(directory)/'journal.db', initialize=True), transport, lambda: None, lambda: 'enhanced', clock=lambda: 1000)
            transport.begin({}, {})
            worker.current, worker.last_poll = {'flight_id':'blocked-report'}, 1000
            thread = threading.Thread(target=worker.once)
            thread.start()
            try:
                self.assertTrue(report_started.wait(1))
                self.assertTrue(progress.wait(.5), 'cloud reporting blocked MAVLink progress')
            finally:
                report_release.set()
                thread.join(2)
                transport.close()

    def test_flight_requires_mission_ack_then_command_ack_and_reached(self):
        self.check_route_completion(False)

    def test_round_trip_does_not_finish_at_outbound_endpoint(self):
        self.check_route_completion(True)

    def check_route_completion(self, round_trip):
        from pymavlink import mavutil
        m = mavutil.mavlink
        packets, sent = [], []
        class Packet(SimpleNamespace):
            def get_type(self): return self.kind
            def get_srcSystem(self): return 1
            def get_srcComponent(self): return 1
        class Writer:
            def __getattr__(self, name): return lambda *args: sent.append((name, args))
        link = SimpleNamespace(mav=Writer(), recv_match=lambda **_: packets.pop(0) if packets else None, close=lambda: None)
        adapter = SimulationFlight(SimulationFlight.ENDPOINT, factory=lambda *a, **k: link, clock=lambda: 0)
        spec = mission()['spec']
        if round_trip:
            spec['route'] += list(reversed(spec['route'][:-1]))
        adapter.begin(FlightRequest(device_id='jetson', request_id='r', action='route', mission_id='m1', revision=1).model_dump(), spec)
        packets.append(Packet(kind='HEARTBEAT', autopilot=m.MAV_AUTOPILOT_ARDUPILOTMEGA, type=m.MAV_TYPE_QUADROTOR, base_mode=0, custom_mode=0))
        adapter.tick()
        self.assertEqual(adapter.state, 'uploading')
        self.assertFalse(any(name == 'command_long_send' for name, _ in sent))
        packets.extend(Packet(kind='MISSION_REQUEST_INT', seq=i) for i in range(len(spec['route']) + 4))
        packets.append(Packet(kind='MISSION_ACK', type=m.MAV_MISSION_ACCEPTED))
        adapter.tick()
        self.assertEqual(adapter.state, 'guided_mode')
        packets.append(Packet(kind='HEARTBEAT', autopilot=3, type=2, base_mode=0, custom_mode=4))
        adapter.tick()
        self.assertEqual(adapter.state, 'arm_ack')
        packets.append(Packet(kind='COMMAND_ACK', command=400, result=0))
        adapter.tick()
        self.assertEqual(adapter.state, 'takeoff_ack')
        packets.extend([Packet(kind='COMMAND_ACK', command=22, result=0), Packet(kind='GLOBAL_POSITION_INT', lat=-353632620, lon=1491652373, relative_alt=15000)])
        adapter.tick()
        packets.append(Packet(kind='HEARTBEAT', autopilot=3, type=2, base_mode=128, custom_mode=3))
        adapter.tick()
        self.assertEqual(adapter.state, 'flying')
        packets.extend(Packet(kind='MISSION_ITEM_REACHED', seq=i) for i in (3, 4, 5))
        result = adapter.tick()
        if round_trip:
            self.assertEqual(result['state'], 'running')
            packets.extend(Packet(kind='MISSION_ITEM_REACHED', seq=i) for i in (6, 7))
            result = adapter.tick()
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['evidence']['waypoint_count'], len(spec['route']))
        self.assertTrue(result['evidence']['mission_upload_ack'])
        self.assertTrue(result['evidence']['holding_at_endpoint'])


if __name__ == '__main__': unittest.main()
