"""普通后台循环与模型任务解耦；断网只记录连接状态，不补传遥测。"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import signal
import threading
import time
from pathlib import Path
from shared.communication.envelope import ProtocolError

from shared.adapters.mavlink_readonly import ReadOnlyMavlink
from shared.adapters.trust import TrustAdapter
from shared.communication.crypto_client import CryptoServiceCodec
from shared.communication.switch import SwitchState
from jetson.edge_agent.catalog import ServiceCatalog
from jetson.edge_agent.client import EdgeClient, SessionTelemetryClient
from jetson.edge_agent.executor import ExecutionJournal, TaskExecutor
from jetson.pi_runner.app import RunnerClient
from jetson.edge_agent.switch import SwitchCoordinator
from shared.communication.auth_client import PolicyProviders
from jetson.edge_agent.auth import EdgeAuth, AuthenticatedEdgeClient


LOG = logging.getLogger('uav-edge')


class BackgroundRuntime:
    def __init__(self, executor, client, telemetry, trust, *, heartbeat_interval=5, claim_interval=3, mission=None, rotation=None):
        self.executor, self.client, self.telemetry, self.trust = executor, client, telemetry, trust
        self.heartbeat_interval, self.claim_interval = heartbeat_interval, claim_interval
        self.stop = threading.Event()
        self.threads = []
        self.telemetry_status = None
        self.telemetry_received_at = None
        self.mission, self.rotation = mission, rotation
        self.flight = None
        self.lifecycle = None
        self.telemetry_client = client

    def heartbeat(self):
        self.client.exchange('heartbeat', lambda: {'trust_start': self.trust.status(), 'agent': 'phase1',
                                            'telemetry': self.sampled_snapshot(),
                                            'protection': self.mission.status if self.mission else None})

    def sampled_snapshot(self):
        snapshot = self.telemetry.snapshot(force=True)
        snapshot['sampled_at'] = time.time()
        return snapshot

    def report_telemetry(self):
        snapshot = self.telemetry.snapshot()
        if snapshot is not None:
            snapshot['sampled_at'] = time.time()
            self.telemetry_status = snapshot
            self.telemetry_received_at = time.time()
            self.telemetry_client.exchange('telemetry', self.sampled_snapshot)

    def position(self):
        snapshot = self.sampled_snapshot()
        if not snapshot['online']:
            return None
        message = snapshot['messages'].get('GLOBAL_POSITION_INT')
        if not message:
            return None
        fields = message['fields']
        return {'position': {'lat': fields['lat'] / 1e7, 'lon': fields['lon'] / 1e7},
            'sampled_at': snapshot['sampled_at'] - message['age_seconds'],
            'speed_mps': (fields['vx'] ** 2 + fields['vy'] ** 2) ** .5 / 100, 'source': 'mavlink-simulation'}

    def tasks(self):
        if self.mission and self.mission.status.get('state') != 'idle':
            return {'state': 'mission_active'}
        return self.executor.once()

    def flight_protection_gate(self, request):
        """操作员授权航线的放行条件；模型不能调用，事务忙或证据缺失就保持等待。"""
        state = self.executor.coordinator.state
        if not state.lock.acquire(blocking=False):
            return None
        try:
            record = self.mission.flight_gate_evidence or {}
            if (record.get('mission_id') != request['mission_id'] or record.get('revision') != request['revision']
                    or record.get('phase') != 'risk' or record.get('workloads_reconciled') is not True):
                return None
            latest = state.latest()
            epoch = state.active()
            if not latest or latest.get('uncertain') or latest['state'] != 'confirmed' or not latest.get('business_receipt') or latest['binding']['epoch'] != epoch or state.profile(epoch) != 'enhanced':
                return None
            transcript = self.client.auth.for_epoch(epoch)
            if min(transcript['session_expires'], transcript['key_expires']) <= time.time() + 10:
                return None
            return {'epoch': epoch, 'auth_policy': state.policy(epoch), 'suite': state.suite(epoch),
                    'protection_profile': 'enhanced', 'identity_verified': True,
                    'new_session_business_verified': True, 'workloads_reconciled': True, 'request_id': latest['binding']['request_id']}
        except ProtocolError:
            return None
        finally:
            state.lock.release()

    def control(self, action):
        return self.lifecycle.control(action) if self.lifecycle else action()

    def loop(self, name, action, interval):
        healthy = None
        while not self.stop.is_set():
            try:
                result = action()
                if isinstance(result, dict) and result.get('state') not in ('idle', 'busy'):
                    # 只记录关联字段，绝不记录场景、明文遥测、密码回执或异常正文。
                    LOG.info('task state=%s task_id=%s', result.get('state'), result.get('task_id', '-'))
                if healthy is False:
                    LOG.info('component=%s connection=restored', name)
                healthy = True
            except Exception as exc:
                if healthy is not False:
                    LOG.warning('component=%s connection=unavailable error_type=%s', name, type(exc).__name__)
                healthy = False
            self.stop.wait(interval)

    def start(self):
        if self.threads:
            raise RuntimeError('runtime already started')
        jobs = [lambda: self.loop('heartbeat', self.heartbeat, self.heartbeat_interval),
                lambda: self.loop('telemetry', self.report_telemetry, self.telemetry.interval),
                lambda: self.loop('tasks', lambda: self.control(self.tasks), self.claim_interval),
                lambda: self.telemetry.run(self.stop)]
        if self.rotation:
            jobs.append(lambda: self.loop('rotation', self.rotation.once, 1))
        if self.mission:
            jobs.append(lambda: self.loop('mission', lambda: self.control(self.mission.once), 2))
        if self.lifecycle:
            jobs.append(lambda: self.loop('workloads', self.lifecycle.once, 3))
        if self.flight:
            jobs.append(lambda: self.loop('operator-flight', self.flight.once, .1))
        self.threads = [threading.Thread(target=job, daemon=True) for job in jobs]
        for thread in self.threads:
            thread.start()

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=20)
        if self.flight:
            self.flight.transport.close()


def main():
    config = json.loads(Path(os.environ['UAV_EDGE_CONFIG']).read_text())
    handler = logging.handlers.RotatingFileHandler(config['log_file'], maxBytes=5 * 1024 * 1024,
                                                   backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.setLevel(logging.INFO)
    LOG.addHandler(handler)
    codec = CryptoServiceCodec(device_id=config['device_id'], key_id=config['key_id'], role='edge',
                               services=config['crypto_services'], token=Path(config['service_token_file']).read_text().strip())
    state = SwitchState(config['switch_state'], codec, config['initial_epoch'], config['initial_suite'])
    identity = config['identity_auth']
    provider = PolicyProviders.from_config(identity)
    auth = EdgeAuth(codec, provider, cloud_id=identity['cloud_id'], credential_version=identity.get('credential_version', '1'), policy_for_epoch=state.policy,
                    profile_for_epoch=state.profile, state_path=config['switch_state'])
    client = AuthenticatedEdgeClient(config['cloud_url'], codec, lambda: coordinator.business_epoch(), auth)
    coordinator = SwitchCoordinator(state, client, codec.ready, provider.ready)
    executor = TaskExecutor(client, coordinator, ServiceCatalog.load(config['catalog']),
                            RunnerClient(config['pi_socket']), ExecutionJournal(config['execution_state']))
    telemetry = ReadOnlyMavlink(config['mavlink_data'], rate_hz=config.get('mavlinkRate', 1),
                                heartbeat_timeout=config.get('heartbeatTimeout', 20))
    runtime = BackgroundRuntime(executor, client, telemetry, TrustAdapter(enabled=config.get('trust_start', False)))
    # 复用已经完成双向认证的会话；控制事务持锁时仍能传递新鲜遥测。
    # 密码服务的持久计数器、重放窗口、有效期和云端设备配额照常校验。
    runtime.telemetry_client = SessionTelemetryClient(config['cloud_url'], client.codec, state.active)
    from jetson.edge_agent.mission import MissionController
    from jetson.edge_agent.rotation import EpochRotation
    from jetson.edge_agent.resources import ResourceSampler
    runtime.mission = MissionController(client, coordinator, executor.runner, executor.catalog, executor.journal, runtime.position, resources=ResourceSampler())
    runtime.rotation = EpochRotation(coordinator, auth)
    from jetson.edge_agent.lifecycle import WorkloadLifecycle
    runtime.lifecycle = WorkloadLifecycle(state, executor.runner, executor.catalog, executor.journal)
    if config.get('manual_simulation_flight'):
        from shared.adapters.simulation_flight import SimulationFlight
        from jetson.edge_agent.flight import FlightController, ThreadedFlightTransport
        runtime.flight = FlightController(client, executor.journal, ThreadedFlightTransport(SimulationFlight(config['manual_simulation_flight'], protection_gate=runtime.flight_protection_gate,
                                         preparation_lead=lambda: runtime.mission.lead_seconds)), runtime.position,
                                         lambda: state.profile(state.active()))
    signal.signal(signal.SIGTERM, lambda *_: runtime.stop.set())
    signal.signal(signal.SIGINT, lambda *_: runtime.stop.set())
    runtime.start()
    try:
        runtime.stop.wait()
    finally:
        runtime.close()


if __name__ == '__main__':
    main()
