import threading
import unittest

from pymavlink.dialects.v20 import ardupilotmega as mavlink

from shared.adapters.mavlink_readonly import ReadOnlyMavlink


class MavlinkReadOnlyTests(unittest.TestCase):
    def test_fragmented_real_frames_timeout_and_rate_limit(self):
        now = [100]
        adapter = ReadOnlyMavlink(clock=lambda: now[0])
        adapter.connected = True
        sender = mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
        frame = sender.heartbeat_encode(2, 3, 0, 0, 4).pack(sender)
        adapter.feed(frame[:5])
        adapter.feed(frame[5:])
        snapshot = adapter.snapshot()
        self.assertTrue(snapshot['online'])
        self.assertIn('HEARTBEAT', snapshot['messages'])
        self.assertIsNone(adapter.snapshot())
        now[0] += 21
        snapshot = adapter.snapshot()
        self.assertFalse(snapshot['online'])
        self.assertEqual(snapshot['messages'], {})
        self.assertIsNone(adapter.parser.file)

    def test_transport_has_no_application_writes_and_control_port_rejected(self):
        stop = threading.Event()
        calls = []
        sender = mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
        frame = sender.heartbeat_encode(2, 3, 0, 0, 4).pack(sender)
        class ReceiveOnlySocket:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def settimeout(self, timeout): pass
            def recv(self, size):
                stop.set()
                return frame
            def send(self, *args): raise AssertionError('flight transport write')
            def sendall(self, *args): raise AssertionError('flight transport write')
        def connect(address, timeout):
            calls.append(address)
            return ReceiveOnlySocket()
        adapter = ReadOnlyMavlink(connect=connect)
        adapter.run(stop)
        self.assertEqual(calls, [('192.168.8.110', 5763)])
        self.assertEqual(adapter.received, 1)
        with self.assertRaises(ValueError):
            ReadOnlyMavlink('tcp:192.168.8.110:5762')


if __name__ == '__main__':
    unittest.main()
