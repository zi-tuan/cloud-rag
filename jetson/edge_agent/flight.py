"""仅领取 Web 操作员指令；与模型规划、只读遥测分别运行。"""
import time
import copy
import threading
from shared.communication.envelope import ProtocolError
from shared.communication.flight import validate_route


class ThreadedFlightTransport:
    """飞控协议独占线程；云端轮询、认证、回执不能阻塞协议响应。"""
    def __init__(self, transport):
        self.transport = transport
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.result = {'state': 'idle'}
        self._state = 'idle'

    @property
    def state(self):
        with self.lock:
            return self._state

    def begin(self, request, spec):
        self.close()
        self.transport.begin(request, spec)
        with self.lock:
            self.result = {'state': 'running', 'evidence': copy.deepcopy(self.transport.evidence)}
            self._state = self.transport.state
        self.stop.clear()
        self.thread = threading.Thread(target=self.run, name='operator-mavlink', daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                result = self.transport.tick()
            except Exception as error:
                result = {'state': 'failed', 'reason': str(error) if isinstance(error, ProtocolError) else 'flight_transport_unavailable',
                          'evidence': self.transport.evidence}
            with self.lock:
                self.result = copy.deepcopy(result)
                self._state = self.transport.state
            if result['state'] != 'running':
                return
            self.stop.wait(.05)

    def tick(self):
        with self.lock:
            return copy.deepcopy(self.result)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                raise ProtocolError('flight_transport_shutdown_pending')
            self.thread = None
        self.transport.close()
        with self.lock:
            self._state = 'idle'


class FlightController:
    def __init__(self, client, journal, transport, position, protection, *, clock=time.time):
        self.client, self.journal, self.transport = client, journal, transport
        self.position, self.protection, self.clock = position, protection, clock
        self.current = None
        self.last_poll = self.last_report = float('-inf')

    def event(self, record, state, reason=None, evidence=None):
        self.journal.save('flight-' + record['flight_id'], {'state': state, 'reason': reason, 'evidence': evidence})
        return self.client.exchange('flight.event', {'flight_id': record['flight_id'], 'state': state, 'reason': reason, 'evidence': evidence})

    def once(self):
        now = self.clock()
        moving = self.current and getattr(self.transport, 'state', None) == 'velocity'
        if not moving and now - self.last_poll >= 5:
            self.last_poll = now
            record = self.client.exchange('flight.poll', {})
            if record and self.current and record['flight_id'] == self.current['flight_id']:
                record = None
            if record:
                identifier = 'flight-' + record['flight_id']
                local = self.journal.get(identifier)
                if record['state'] != 'queued' or local:
                    # 领取应答/进程丢失后绝不重新发送解锁和起飞。
                    if record['state'] == 'queued':
                        self.client.exchange('flight.claim', {'flight_id': record['flight_id']})
                    if local and local['state'] in ('completed', 'failed', 'interrupted'):
                        return self.event(record, local['state'], local.get('reason'), local.get('evidence'))
                    return self.event(record, 'interrupted', 'flight_restart_requires_manual_reconciliation')
                claimed = self.client.exchange('flight.claim', {'flight_id': record['flight_id']})
                if claimed['state'] != 'claimed':
                    return claimed
                self.journal.save(identifier, {'state': 'claimed'})
                if self.current:
                    self.transport.close()
                    self.current = None
                try:
                    sample = self.position()
                    if not sample or not 0 <= self.clock() - sample['sampled_at'] <= 5:
                        raise ProtocolError('flight_telemetry_stale')
                    request = record['request']
                    if request['action'] == 'route':
                        mission = self.client.exchange('mission.poll', {})
                        if not mission or mission['mission_id'] != request['mission_id'] or mission['revision'] != request['revision'] or mission['spec'] != record['route_spec']:
                            raise ProtocolError('flight_route_revision_changed')
                        validate_route(mission, self.position(), self.protection(), self.clock())
                    self.transport.begin(request, record['route_spec'])
                    self.current = record
                except Exception as error:
                    return self.event(record, 'failed', str(error) if isinstance(error, ProtocolError) else 'flight_transport_unavailable')
        if not self.current:
            return {'state': 'idle'}
        try:
            result = self.transport.tick()
        except Exception as error:
            result = {'state': 'failed', 'reason': str(error) if isinstance(error, ProtocolError) else 'flight_transport_unavailable',
                      'evidence': getattr(self.transport, 'evidence', None)}
        terminal = result['state'] != 'running'
        if terminal or getattr(self.transport, 'state', None) != 'velocity' and now - self.last_report >= 5:
            self.last_report = now
            record = self.current
            # 先写终态；回执丢失也不会重复执行飞行动作。
            if terminal:
                self.current = None
                self.transport.close()
            return self.event(record, result['state'], result.get('reason'), result.get('evidence'))
        return {'state': 'busy'}
