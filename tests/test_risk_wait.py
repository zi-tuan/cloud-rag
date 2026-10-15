import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from shared.adapters.simulation_flight import SimulationFlight
from shared.communication.flight import FlightRequest
from shared.communication.mission import distance
from jetson.edge_agent.runtime import BackgroundRuntime
from test_flight import mission


class RiskWaitTests(unittest.TestCase):
    def test_mavlink_stops_outside_C_until_verified_then_confirms_resume(self):
        from pymavlink import mavutil
        packets, sent, now, proof = [], [], [100], [None]
        class Packet(SimpleNamespace):
            def get_type(self): return self.kind
            def get_srcSystem(self): return 1
            def get_srcComponent(self): return 1
        class Writer:
            def __getattr__(self, name): return lambda *args: sent.append((name, args))
        link = SimpleNamespace(mav=Writer(), recv_match=lambda **_: packets.pop(0) if packets else None, close=lambda: None)
        adapter = SimulationFlight(SimulationFlight.ENDPOINT, factory=lambda *a, **k: link,
            clock=lambda: now[0], protection_gate=lambda request: proof[0])
        spec = mission()['spec']
        request = FlightRequest(device_id='jetson', request_id='r', action='route', mission_id='m1', revision=1, wait_before_risk=True).model_dump()
        adapter.begin(request, spec)
        adapter.target = (1, 1)
        adapter.upload()
        sequence, gate = next(iter(adapter.gates.items()))
        self.assertGreaterEqual(distance(gate['position'], spec['risks'][0]['center']) - 200, 50)
        self.assertEqual(adapter.items[sequence][0], mavutil.mavlink.MAV_CMD_NAV_LOITER_UNLIM)
        adapter.state, adapter.deadline, adapter.last_heartbeat = 'flying', 1000, 100
        packets.extend([Packet(kind='MISSION_CURRENT', seq=sequence), Packet(kind='GLOBAL_POSITION_INT',
            lat=round(gate['position']['lat']*1e7), lon=round(gate['position']['lon']*1e7), relative_alt=15000, vx=0, vy=0)])
        self.assertEqual(adapter.tick()['evidence']['stage'], 'waiting_enhanced')
        self.assertFalse(any(name == 'mission_set_current_send' for name, _ in sent))
        now[0] += 1
        adapter.tick()
        self.assertFalse(any(name == 'mission_set_current_send' for name, _ in sent))
        proof[0] = {'epoch': 'verified-enhanced'}
        now[0] += 2
        adapter.tick()
        self.assertEqual(adapter.state, 'waiting_enhanced', 'stale position must not release')
        packets.append(Packet(kind='GLOBAL_POSITION_INT', lat=round(gate['position']['lat']*1e7), lon=round(gate['position']['lon']*1e7), relative_alt=15000, vx=0, vy=0))
        adapter.tick()
        self.assertEqual(adapter.state, 'resuming_risk')
        self.assertEqual([args for name, args in sent if name == 'mission_set_current_send'], [(1, 1, sequence + 1)])
        packets.append(Packet(kind='MISSION_CURRENT', seq=sequence + 1))
        adapter.tick()
        self.assertEqual(adapter.state, 'flying')
        self.assertIn('resume_confirmed_at', adapter.evidence['risk_waits'][0])
        packets.extend(Packet(kind='MISSION_ITEM_REACHED', seq=seq) for seq in adapter.route_sequences)
        self.assertEqual(adapter.tick()['state'], 'completed')

    def test_gate_requires_matching_revision_confirmed_business_and_live_session(self):
        latest = {'state': 'confirmed', 'business_receipt': 'verified', 'binding': {'epoch': 'e', 'request_id': 'switch'}}
        state = SimpleNamespace(lock=threading.RLock(), latest=lambda: latest, active=lambda: 'e',
            profile=lambda epoch: 'enhanced', policy=lambda epoch: 'RSA', suite=lambda epoch: 'AES-256-GCM')
        runtime = BackgroundRuntime(SimpleNamespace(coordinator=SimpleNamespace(state=state)),
            SimpleNamespace(auth=SimpleNamespace(for_epoch=lambda epoch: {'session_expires': 2000, 'key_expires': 2000})), None, None,
            mission=SimpleNamespace(flight_gate_evidence={'mission_id': 'm1', 'revision': 1, 'phase': 'risk', 'epoch': 'e', 'workloads_reconciled': True}))
        request = {'mission_id': 'm1', 'revision': 1}
        with patch('jetson.edge_agent.runtime.time.time', return_value=1000):
            self.assertTrue(runtime.flight_protection_gate(request))
            runtime.mission.flight_gate_evidence['workloads_reconciled'] = False
            self.assertIsNone(runtime.flight_protection_gate(request))
            runtime.mission.flight_gate_evidence['workloads_reconciled'] = True
            self.assertIsNone(runtime.flight_protection_gate({**request, 'revision': 2}))
            latest['business_receipt'] = None
            self.assertIsNone(runtime.flight_protection_gate(request))
            latest['business_receipt'], latest['uncertain'] = 'verified', True
            self.assertIsNone(runtime.flight_protection_gate(request))
            latest['uncertain'] = False
        with patch('jetson.edge_agent.runtime.time.time', return_value=1995):
            self.assertIsNone(runtime.flight_protection_gate(request))

    def test_waiting_is_only_an_operator_route_option(self):
        with self.assertRaises(ValueError):
            FlightRequest(device_id='jetson', request_id='r', action='takeoff', wait_before_risk=True)


if __name__ == '__main__':
    unittest.main()
