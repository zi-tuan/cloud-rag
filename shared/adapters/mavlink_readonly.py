"""仅接收仿真数据：不连接控制端口，不创建可写 MAVLink transport。"""
from __future__ import annotations

import socket
import threading
import time

from pymavlink.dialects.v20 import ardupilotmega as mavlink


class ReadOnlyMavlink:
    def __init__(self, endpoint='tcp:192.168.8.110:5763', *, rate_hz=1, heartbeat_timeout=20,
                 clock=time.monotonic, connect=socket.create_connection):
        parts = endpoint.split(':')
        if len(parts) != 3 or parts[0] != 'tcp' or not parts[1] or parts[2] != '5763':
            raise ValueError('MVP accepts only the configured simulation data port 5763')
        if not 0 < rate_hz <= 10 or not 1 <= heartbeat_timeout <= 300:
            raise ValueError('invalid telemetry timing')
        self.address = (parts[1], int(parts[2]))
        self.interval, self.heartbeat_timeout = 1 / rate_hz, heartbeat_timeout
        self.clock, self.connect = clock, connect
        self.lock = threading.Lock()
        self.last_heartbeat = None
        self.latest = {}
        self.received = 0
        self.invalid = 0
        self.connected = False
        self.last_snapshot = float('-inf')
        self._reset_parser()

    def _reset_parser(self):
        # parser 没有 writer；即使误用库的 send，也没有飞控 socket 可写。
        self.parser = mavlink.MAVLink(None)
        self.parser.robust_parsing = True
        if mavlink.MAVLINK_IGNORE_CRC:
            raise ValueError('MAVLink CRC validation must remain enabled')

    def feed(self, data):
        with self.lock:
            for message in self.parser.parse_buffer(data) or []:
                kind = message.get_type()
                if kind == 'BAD_DATA':
                    self.invalid += 1
                    continue
                if message.get_srcComponent() != 1:
                    continue
                self.received += 1
                if kind == 'HEARTBEAT':
                    self.last_heartbeat = self.clock()
                if kind in ('HEARTBEAT', 'GLOBAL_POSITION_INT', 'GPS_RAW_INT', 'SYS_STATUS', 'ATTITUDE', 'VFR_HUD'):
                    self.latest[kind] = {'source_system': message.get_srcSystem(),
                                         'received_monotonic': self.clock(), 'fields': message.to_dict()}

    def snapshot(self, *, force=False):
        with self.lock:
            now = self.clock()
            if not force and now - self.last_snapshot < self.interval:
                return None
            self.last_snapshot = now
            heartbeat_age = None if self.last_heartbeat is None else now - self.last_heartbeat
            # 过期遥测不作为当前事实提供给模型，也不在恢复网络后补发。
            messages = {kind: {'source_system': item['source_system'], 'age_seconds': now - item['received_monotonic'],
                               'fields': dict(item['fields'])}
                        for kind, item in self.latest.items() if now - item['received_monotonic'] <= self.heartbeat_timeout}
            return {'source': 'mavlink-simulation', 'read_only': True,
                    'online': self.connected and heartbeat_age is not None and heartbeat_age <= self.heartbeat_timeout,
                    'heartbeat_age_seconds': heartbeat_age, 'received_messages': self.received,
                    'invalid_frames': self.invalid, 'messages': messages}

    def run(self, stop):
        while not stop.is_set():
            try:
                with self.connect(self.address, timeout=5) as stream:
                    stream.settimeout(1)
                    with self.lock:
                        self._reset_parser()
                        self.latest.clear()
                        self.last_heartbeat = None
                        self.connected = True
                    while not stop.is_set():
                        try:
                            data = stream.recv(65536)
                        except socket.timeout:
                            continue
                        if not data:
                            break
                        self.feed(data)
            except OSError:
                pass
            finally:
                with self.lock:
                    self.connected = False
            stop.wait(2)
