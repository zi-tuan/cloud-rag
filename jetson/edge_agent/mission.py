"""持续任务控制器：模型阶段规划，确定性风险守卫与统一切换执行。"""
import hashlib
import logging
import time
import uuid
from shared.communication.envelope import ProtocolError, canonical
from shared.communication.mission import risk_context, validate_plan, preparation_window
from shared.communication.protection import PROFILES
from jetson.edge_agent.lifecycle import retire_unused

LOG = logging.getLogger('uav-edge')


class MissionController:
    def __init__(self, client, coordinator, runner, catalog, journal, position, *, resources=None, clock=time.time, monotonic=time.monotonic):
        self.client, self.coordinator, self.runner, self.catalog, self.journal = client, coordinator, runner, catalog, journal
        self.position, self.resources = position, resources or (lambda: {'available': False})
        self.clock, self.monotonic = clock, monotonic
        self.boot_id = uuid.uuid4().hex
        self.status = {'state': 'idle'}
        self.flight_gate_evidence = None
        self.lead_seconds = 30

    def send(self, kind, mission, **payload):
        return self.client.exchange(kind, {'mission_id': mission['mission_id'], 'revision': mission['revision'], **payload})

    def finish_pending(self, mission, identifier, record):
        """业务切换确认后保留原过渡期限，旧 Pod 消失才发布阶段成功和飞行放行证据。"""
        pending = record['pending']
        state = self.coordinator.state
        with state.lock:
            result = self.coordinator.start(pending['request_id'], pending['epoch'], pending['target']['suite'],
                auth_policy=pending['target']['auth_policy'], protection_profile=pending['target']['protection_profile'])
            if result['state'] == 'succeeded' and self.clock() < state.get(pending['request_id'])['retire_at']:
                self.status = {'state': 'waiting_retirement', 'phase': pending['phase'], 'mission_id': mission['mission_id']}
                return self.status
        if result['state'] == 'succeeded':
            # 只持有运行时的控制串行锁；缩容等待不能阻止同组合的会话续期。
            if retire_unused(state, self.runner, self.catalog)['state'] != 'reconciled':
                self.status = {'state': 'waiting_retirement', 'phase': pending['phase'], 'mission_id': mission['mission_id']}
                return self.status
            with state.lock:
                epoch = state.active()
                actual = {'auth_policy': state.policy(epoch), 'suite': state.suite(epoch), 'protection_profile': state.profile(epoch)}
                latest = state.latest()
                if actual != pending['target'] or not latest or latest.get('uncertain') or latest['state'] != 'confirmed':
                    return {'state': 'waiting_switch_transaction'}
                # 缩容期间若已正常轮换，用当前已确认会话的业务证据验收同一阶段。
                request_id = pending['request_id'] if epoch == pending['epoch'] else latest['binding']['request_id']
                same_revision = pending.get('revision') == mission['revision']
                if same_revision:
                    self.send('mission.receipt', mission, stage=pending['phase'], request_id=request_id, workloads_reconciled=True)
                    self.flight_gate_evidence = {'mission_id': mission['mission_id'], 'revision': mission['revision'],
                        'phase': pending['phase'], 'epoch': epoch, 'workloads_reconciled': True}
                if pending['target']['protection_profile'] == 'enhanced':
                    record['durations'].append(self.clock() - pending['started_at'])
                record['durations'] = record['durations'][-100:]
                record.update(pending=None, preparation=None, effective_phase=pending['phase'] if same_revision else None)
                LOG.info('mission=%s phase=%s protection_profile=%s epoch=%s effective=true workloads_reconciled=true',
                    mission['mission_id'], pending['phase'], result['protection_profile'], epoch)
        elif result['state'] == 'failed':
            record.update(pending=None, preparation=None, retry_after=self.clock() + 10)
            self.send('mission.unmet', mission, reason='protection_target_not_effective')
        self.journal.save(identifier, record)
        return result

    def once(self):
        # 先读取本地新鲜遥测，网络恢复后不能沿用缓存退出计时。
        mission = self.client.exchange('mission.poll', {})
        if mission is None:
            self.status = {'state': 'idle'}
            return self.status
        sample = self.position()
        identifier = 'mission-' + mission['mission_id']
        record = self.journal.get(identifier) or {'visited': [], 'sequence': 0, 'plan': None, 'pending': None, 'durations': []}
        if record.get('boot_id') != self.boot_id or record.get('revision') != mission['revision']:
            record.update(boot_id=self.boot_id, revision=mission['revision'], outside_since=None)
            if record.get('plan_revision') != mission['revision']:
                self.flight_gate_evidence = None
                record.update(plan=None, planner_id=None, plan_revision=mission['revision'], visited=[], effective_phase=None)
                if not record['pending']:
                    record['preparation'] = None
        state = self.coordinator.state
        epoch = state.active()
        current = {'auth_policy': state.policy(epoch), 'suite': state.suite(epoch), 'protection_profile': state.profile(epoch)}
        if sample is None or not 0 <= self.clock() - sample['sampled_at'] <= 5:
            record['outside_since'] = None
            self.journal.save(identifier, record)
            self.status = {'state': 'waiting_telemetry', 'mission_id': mission['mission_id'], 'reason': 'telemetry_stale_downgrade_blocked'}
            return self.status
        observed = self.send('mission.observe', mission, position=sample['position'], sampled_at=sample['sampled_at'],
            preparation_durations=record['durations'], speed_mps=sample.get('speed_mps', 0),
            preparation=record.get('preparation'))
        risk = risk_context(mission['spec'], sample['position'], observed['next_waypoint'])
        # 独立遥测比控制事务更频繁，不能丢弃云端已通过认证采样确认的穿越。
        record['visited'] = sorted(set(record['visited']) | set(observed['visited']) | set(risk['inside']))
        stable = risk['outside_margin'] and not mission['spec']['alarms'] and (bool(record['visited']) or current['protection_profile'] == 'enhanced')
        now = self.monotonic()
        if stable:
            if record.get('outside_since') is None:
                record['outside_since'] = now
        else:
            record['outside_since'] = None
        timing = preparation_window(record['durations'])
        lead = timing['lead_seconds']
        self.lead_seconds = lead
        speed = max(sample.get('speed_mps', 0), mission['spec']['resources']['cruise_speed_mps'])
        entry = risk['entry_distance_m']
        eta = None if entry is None else entry / speed
        imminent = bool(risk['inside']) or eta is not None and eta <= lead
        phase = 'risk' if imminent else 'exit' if record['visited'] else 'transit'
        if phase == 'exit' and (not stable or now - record['outside_since'] < 10):
            if stable and current['protection_profile'] == 'lightweight':
                # 已在区外恢复轻量时，重启只重新积累证据，不为等待计时再次增强。
                self.journal.save(identifier, record)
                self.status = {'state': 'waiting_exit_evidence', 'mission_id': mission['mission_id']}
                return self.status
            phase = 'risk'
        if phase == 'transit' and current['protection_profile'] == 'enhanced' and (not stable or now - record['outside_since'] < 10):
            phase = 'risk'
        if record['pending']:
            return self.finish_pending(mission, identifier, record)
        usage = self.resources()
        if usage.get('available') is not True:
            self.send('mission.unmet', mission, reason='resource_measurement_unavailable')
            self.status = {'state': 'waiting_resources', 'mission_id': mission['mission_id']}
            self.journal.save(identifier, record)
            return self.status
        # 只在规格、资源压力或能力集合明显变化时重新调用模型。
        budget = mission['spec']['resources']
        pressure = usage.get('cpu_millicores', 0) > budget['cpu_budget_millicores'] or usage.get('memory_mib', 0) > budget['memory_budget_mib']
        context_key = hashlib.sha256(canonical({'revision': mission['revision'], 'resource_pressure': pressure,
            'catalog': self.catalog.public()})).hexdigest()
        if record.get('context_key') != context_key:
            record.update(plan=None, context_key=context_key, planner_id=uuid.uuid4().hex, planner_started=False)
        if record['plan'] is None:
            context = {'spec': mission['spec'], 'position': sample, 'risk': risk, 'resources': usage,
                'resource_pressure': pressure, 'current_protection': current, 'registered_profiles': PROFILES,
                'services': self.catalog.public(), 'lead_seconds': lead, 'eta_seconds': eta}
            if not record.get('planner_started'):
                record['planner_started'] = True
                self.journal.save(identifier, record)
                outcome = self.runner.start({'task_id': record['planner_id'], 'scenario': mission['spec']['goal'],
                    'protection_context': context, 'current_combination': current})
            else:
                outcome = self.runner.lookup(record['planner_id'])
            if outcome.get('state') == 'completed':
                from jetson.edge_agent.resources import service_fits
                try:
                    plan = validate_plan(outcome['selection']['plan'])
                    entries = [self.catalog.for_suite(p['target']['suite']) for p in plan['phases']]
                    permitted = all(e.get('registry_verified') is True and service_fits(e, budget) for e in entries)
                except (ProtocolError, KeyError, TypeError, ValueError):
                    permitted = False
                if pressure or not permitted:
                    self.send('mission.unmet', mission, reason='planning_or_resource_constraint_unsatisfied')
                    self.status = {'state': 'unmet', 'mission_id': mission['mission_id']}
                    self.journal.save(identifier, record)
                    return self.status
                self.send('mission.plan', mission, plan=plan, model_reply=outcome['model_reply'])
                record['plan'] = plan
            elif outcome.get('state') == 'failed' or pressure:
                self.send('mission.unmet', mission, reason='planning_or_resource_constraint_unsatisfied')
                self.status = {'state': 'unmet', 'mission_id': mission['mission_id']}
            elif outcome.get('state') == 'unknown':
                self.send('mission.unmet', mission, reason='model_execution_requires_manual_reconciliation')
                self.status = {'state': 'unmet', 'mission_id': mission['mission_id']}
            else:
                self.status = {'state': 'planning', 'mission_id': mission['mission_id']}
            self.journal.save(identifier, record)
            if record['plan'] is None:
                return self.status
        target = next(p['target'] for p in record['plan']['phases'] if p['stage'] == phase)
        baseline = record['plan']['phases'][0]['target']
        has_baseline = any(r['revision'] == mission['revision'] and r['stage'] == 'transit' for r in mission['receipts'])
        if not has_baseline and current == baseline and not risk['inside']:
            if retire_unused(state, self.runner, self.catalog)['state'] != 'reconciled':
                self.journal.save(identifier, record)
                self.status = {'state': 'waiting_retirement', 'phase': 'transit', 'mission_id': mission['mission_id']}
                return self.status
            acknowledged = self.send('mission.receipt', mission, stage='transit', request_id=None, workloads_reconciled=True)
            if not any(r['revision'] == mission['revision'] and r['stage'] == 'transit' for r in acknowledged['receipts']):
                self.journal.save(identifier, record)
                return {'state': 'waiting_receipt', 'reason': 'current_revision_receipt_missing'}
            if record.get('effective_phase') is None:
                record['effective_phase'] = 'transit'
        needs_switch = current != target or record.get('effective_phase') != phase
        if not record['pending'] and record.get('preparation') and (not needs_switch or record['preparation']['target'] != target):
            record['preparation'] = None
        if not record['pending'] and needs_switch and self.clock() >= record.get('retry_after', 0):
            if not record.get('preparation') or record['preparation']['target'] != target:
                record['preparation'] = {'target': target, 'phase': phase, 'started_at': self.clock()}
                self.journal.save(identifier, record)
            self.send('mission.observe', mission, position=sample['position'], sampled_at=sample['sampled_at'],
                preparation_durations=record['durations'], speed_mps=sample.get('speed_mps', 0), preparation=record['preparation'])
            # 候选服务的拉起/健康等待不持有会话事务锁，避免阻止现有会话续期。
            # 准备完成后仍在共享事务锁内重新核对当前会话和未完成事务。
            try:
                service = self.catalog.for_suite(target['suite'])
                self.runner.activate(service['id'])
                self.runner.authentication(target['auth_policy'])
                ready = self.coordinator.ready(target['suite']) and self.coordinator.auth_ready(target['auth_policy'])
            except (ProtocolError, OSError):
                ready = False
            if not ready:
                self.send('mission.unmet', mission, reason='registered_service_not_ready')
                self.journal.save(identifier, record)
                return {'state': 'waiting_service'}
        # 单个控制器与人工任务、周期轮换共用同一事务锁。
        with state.lock:
            latest = state.latest()
            current = {'auth_policy': state.policy(state.active()), 'suite': state.suite(state.active()), 'protection_profile': state.profile(state.active())}
            if current == target and record.get('effective_phase') == phase:
                self.status = {'state': 'monitoring', 'phase': phase, 'protection_profile': current['protection_profile'], 'eta_seconds': eta, 'lead_seconds': lead}
                self.journal.save(identifier, record)
                return self.status
            if latest and (latest.get('uncertain') or latest['state'] not in ('confirmed', 'aborted') or self.clock() < latest.get('retire_at', 0)):
                self.journal.save(identifier, record)
                return {'state': 'waiting_switch_transaction'}
            if self.clock() < record.get('retry_after', 0):
                return {'state': 'waiting_retry', 'reason': 'collecting_fresh_switch_evidence'}
            record['sequence'] += 1
            request = mission['mission_id'] + ':' + str(mission['revision']) + ':' + str(record['sequence'])
            record['pending'] = {'request_id': request, 'epoch': 'mission-' + uuid.uuid5(uuid.NAMESPACE_URL, request).hex,
                'target': target, 'phase': phase, 'started_at': record['preparation']['started_at'], 'revision': mission['revision']}
            self.journal.save(identifier, record)
            self.status = {'state': 'preparing', 'phase': phase, 'mission_id': mission['mission_id']}
            return self.status
