import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from shared.adapters.trust import TrustAdapter
from jetson.edge_agent.runtime import BackgroundRuntime


class RuntimeTests(unittest.TestCase):
    def test_cloud_receives_fresh_telemetry_without_waiting_for_heartbeat(self):
        import tempfile
        from pathlib import Path
        from cloud.cloud_api.tasks import TaskStore
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(Path(directory)/'tasks.db')
            store.heartbeat('edge', {'trust_start': 'unknown', 'telemetry': {'sampled_at': 990}}, now=990)
            self.assertTrue(store.telemetry('edge', {'sampled_at': 1000, 'online': True}, now=1001))
            device = store.device('edge', now=1001)
            self.assertEqual(device['status']['telemetry']['sampled_at'], 1000)
            self.assertEqual(device['status']['trust_start'], 'unknown')
            self.assertFalse(store.telemetry('edge', {'sampled_at': 999}, now=1001))
            self.assertFalse(store.telemetry('edge', {'sampled_at': 900}, now=1001))
    def test_payload_sample_is_taken_after_authentication_wait(self):
        from jetson.edge_agent.auth import AuthenticatedEdgeClient
        now = [1000]
        captured = []
        def ensure(*_): now[0] += 15
        def seal(kind, payload, **kwargs):
            self.assertIsInstance(payload, dict)
            captured.append(payload)
            return {'header': {'message_id': 'm'}}
        codec = SimpleNamespace(device_id='edge', key_id='key', role='edge', seal=seal, open=lambda _: ({'reply_to': 'm', 'kind': 'business.reply', 'epoch': 'epoch'}, {'ok': True, 'result': {}}))
        auth = SimpleNamespace(provider=None, lock=threading.RLock(), ensure=ensure)
        client = AuthenticatedEdgeClient('http://cloud/api/edge/exchange', codec, lambda: 'epoch', auth, transport=lambda _: {})
        client.codec = codec
        client.exchange('heartbeat', lambda: {'sampled_at': now[0]})
        self.assertEqual(captured[0]['sampled_at'], 1015)
    def test_position_reads_live_mavlink_while_report_is_delayed(self):
        from shared.adapters.mavlink_readonly import ReadOnlyMavlink
        from pymavlink.dialects.v20 import ardupilotmega as mavlink
        now = [100.0]
        reader = ReadOnlyMavlink(clock=lambda: now[0])
        reader.connected = True
        sender = mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
        heartbeat = sender.heartbeat_encode(2, 3, 0, 0, 4)
        position = sender.global_position_int_encode(100, -353632620, 1491652373, 584170, 0, 0, 0, 0, 0)
        reader.feed(heartbeat.pack(sender) + position.pack(sender))
        runtime = BackgroundRuntime(None, type('Client', (), {'exchange': lambda *args: None})(), reader, TrustAdapter())
        with patch('jetson.edge_agent.runtime.time.time', return_value=1000): runtime.report_telemetry()
        now[0] = 115
        reader.feed(heartbeat.pack(sender) + position.pack(sender))
        with patch('jetson.edge_agent.runtime.time.time', return_value=1015):
            self.assertGreaterEqual(runtime.position()['sampled_at'], 1010)
    def test_slow_or_failed_model_does_not_stop_heartbeat_and_telemetry(self):
        release = threading.Event()
        heartbeats = threading.Event()
        telemetry = threading.Event()
        calls = []
        class Executor:
            def once(self):
                release.wait(3)
                raise TimeoutError('model unavailable')
        class Client:
            def exchange(self, kind, payload):
                calls.append(kind)
                if calls.count('heartbeat') >= 3:
                    heartbeats.set()
                if calls.count('telemetry') >= 3:
                    telemetry.set()
        class Telemetry:
            interval = 0.01
            def snapshot(self): return {'online': False, 'messages': {}}
            def run(self, stop): stop.wait()
        runtime = BackgroundRuntime(Executor(), Client(), Telemetry(), TrustAdapter(),
                                    heartbeat_interval=0.01, claim_interval=0.01)
        runtime.start()
        try:
            self.assertTrue(heartbeats.wait(2))
            self.assertTrue(telemetry.wait(2))
        finally:
            release.set()
            runtime.close()
        self.assertTrue(all(not thread.is_alive() for thread in runtime.threads))


if __name__ == '__main__':
    unittest.main()
