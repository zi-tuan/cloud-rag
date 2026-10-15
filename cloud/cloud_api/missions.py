"""持续任务 Repository 与云端验收，成功只能由认证业务证据产生。"""
import json
import time
import uuid
from shared.communication.database import SQLiteDatabase
from shared.communication.envelope import ProtocolError, canonical, IDENTIFIER
from shared.communication.mission import MissionSpec, Point, advance_route, risk_context, validate_plan, preparation_window


class MissionStore:
    def __init__(self, path, *, clock=time.time):
        self.db, self.clock = SQLiteDatabase(path), clock
        with self.db.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS missions (id TEXT PRIMARY KEY, device TEXT NOT NULL, request_id TEXT UNIQUE NOT NULL, data TEXT NOT NULL)')
        # 服务重启必须重新收集连续退出证据。
        with self.db.connect() as db:
            for identifier, data in db.execute('SELECT id,data FROM missions').fetchall():
                record = json.loads(data)
                record['outside_since'] = None
                record.setdefault('next_waypoint', 0)
                db.execute('UPDATE missions SET data=? WHERE id=?', (canonical(record).decode(), identifier))

    def save(self, record):
        with self.db.connect() as db:
            db.execute('UPDATE missions SET data=? WHERE id=?', (canonical(record).decode(), record['mission_id']))
        return record

    def get(self, identifier, device):
        with self.db.connect() as db:
            row = db.execute('SELECT data FROM missions WHERE id=? AND device=?', (identifier, device)).fetchone()
        if row is None:
            raise ProtocolError('unknown mission')
        return json.loads(row[0])

    def active(self, device):
        with self.db.connect() as db:
            records = [json.loads(row[0]) for row in db.execute('SELECT data FROM missions WHERE device=? ORDER BY rowid DESC', (device,))]
        return next((r for r in records if r['state'] in ('queued', 'running', 'unmet', 'paused')), None)

    def submit(self, device, request_id, spec):
        spec = MissionSpec.model_validate(spec).model_dump()
        if not IDENTIFIER.fullmatch(request_id):
            raise ProtocolError('invalid mission request')
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM missions WHERE request_id=?', (request_id,)).fetchone()
            if row:
                record = json.loads(row[0])
                if record['spec'] != spec or record['device_id'] != device:
                    raise ProtocolError('mission request conflict')
                return record
            rows = db.execute('SELECT data FROM missions WHERE device=?', (device,)).fetchall()
            if any(json.loads(r[0])['state'] in ('queued', 'running', 'unmet', 'paused') for r in rows):
                raise ProtocolError('another mission is active')
            identifier = uuid.uuid4().hex
            record = {'mission_id': identifier, 'device_id': device, 'request_id': request_id, 'spec': spec,
                'revision': 1, 'state': 'queued', 'plan': None, 'plans': [], 'visited': [], 'receipts': [], 'next_waypoint': 0,
                'outside_since': None, 'last_observation': None, 'violations': [], 'created_at': self.clock()}
            db.execute('INSERT INTO missions VALUES (?,?,?,?)', (identifier, device, request_id, canonical(record).decode()))
        return record

    def revise(self, identifier, device, spec):
        record = self.get(identifier, device)
        if record['state'] in ('succeeded', 'cancelled'):
            raise ProtocolError('mission is terminal')
        spec = MissionSpec.model_validate(spec).model_dump()
        if record['spec'] == spec:
            return record
        record.update(spec=spec, revision=record['revision'] + 1,
            plan=None, visited=[], outside_since=None, next_waypoint=0, last_observation=None)
        return self.save(record)

    def guard_switch(self, binding):
        record = self.active(binding['device_id'])
        if record is None or binding['protection_profile'] != 'lightweight':
            return
        observation = record['last_observation']
        if observation is None or self.clock() - observation['sampled_at'] > 5:
            raise ProtocolError('downgrade blocked by stale mission telemetry')
        if record['spec']['alarms'] or observation['risk']['inside']:
            raise ProtocolError('downgrade blocked by risk or alarm')
        if observation['protection_profile'] == 'enhanced' or any(r['protection_profile'] == 'enhanced' for r in record['receipts']):
            entry = observation['risk']['entry_distance_m']
            speed = record['spec']['resources']['cruise_speed_mps']
            timing = preparation_window(record.get('preparation_durations', []))
            speed = max(speed, observation.get('speed_mps', 0))
            if entry is not None and entry / speed <= timing['lead_seconds']:
                raise ProtocolError('downgrade blocked by upcoming risk')
            if not observation['risk']['outside_margin'] or record['outside_since'] is None or self.clock() - record['outside_since'] < 10:
                raise ProtocolError('downgrade requires stable risk exit')

    def dispatch(self, kind, device, body, header, switch, auth):
        if kind == 'mission.poll':
            return self.active(device)
        record = self.get(body['mission_id'], device)
        if body.get('revision') != record['revision']:
            raise ProtocolError('mission revision mismatch')
        if kind == 'mission.plan':
            plan = validate_plan(body['plan'])
            record.update(plan=plan, state='running' if not record['violations'] else 'unmet')
            record['plans'].append({'revision': record['revision'], 'plan': plan, 'model_reply': body['model_reply'], 'created_at': self.clock()})
        elif kind == 'mission.unmet':
            record.update(state='unmet', reason=body['reason'])
        elif kind == 'mission.observe':
            now = self.clock()
            sampled = body['sampled_at']
            if 'preparation_durations' in body:
                preparation_window(body['preparation_durations'])
                record['preparation_durations'] = body['preparation_durations']
                record['preparation'] = body['preparation']
            previous = record['last_observation']
            # 控制线程排队前采样的旧位置不能覆盖独立遥测通道的新证据。
            if isinstance(sampled, (int, float)) and previous and sampled <= previous['sampled_at']:
                return self.save(record)
            if not isinstance(sampled, (int, float)) or not 0 <= now - sampled <= 5:
                record['outside_since'] = None
                record['reason'] = 'telemetry_stale_downgrade_blocked'
            else:
                if record.get('reason') == 'telemetry_stale_downgrade_blocked':
                    record.pop('reason')
                position = Point.model_validate(body['position']).model_dump()
                record['next_waypoint'] = advance_route(record['spec']['route'], position, record['next_waypoint'])
                risk = risk_context(record['spec'], position, record['next_waypoint'])
                profile = switch.state.profile(header['epoch'])
                session = auth.get(header['auth_session_id'])
                if session['protection_profile'] != profile:
                    raise ProtocolError('actual protection profile mismatch')
                if risk['inside'] and profile != 'enhanced':
                    if 'risk_entered_without_enhanced_protection' not in record['violations']:
                        record['violations'].append('risk_entered_without_enhanced_protection')
                    record['state'] = 'unmet'
                record['visited'] = sorted(set(record['visited']) | set(risk['inside']))
                previous = record['last_observation']
                stable = risk['outside_margin'] and not record['spec']['alarms'] and (bool(record['visited']) or profile == 'enhanced')
                gap = previous is None or sampled - previous['sampled_at'] > 5 or sampled < previous['sampled_at']
                record['outside_since'] = (sampled if gap or record['outside_since'] is None else record['outside_since']) if stable else None
                record['last_observation'] = {'position': position, 'sampled_at': sampled, 'risk': risk,
                    'epoch': header['epoch'], 'protection_profile': profile, 'session_expires': session['session_expires'], 'key_expires': session['key_expires'],
                    'speed_mps': max(0, body.get('speed_mps', 0))}
                timing = preparation_window(record.get('preparation_durations', []))
                speed = max(record['spec']['resources']['cruise_speed_mps'], record['last_observation']['speed_mps'])
                entry = risk['entry_distance_m']
                eta = None if entry is None else entry / speed
                record['protection_timing'] = {**timing, 'eta_seconds': eta, 'entry_distance_m': entry,
                    'speed_mps': speed, 'imminent': bool(risk['inside']) or eta is not None and eta <= timing['lead_seconds']}
                phases = {r['stage'] for r in record['receipts'] if r['revision'] == record['revision'] and r.get('workloads_reconciled') is True}
                if risk['arrival'] and set(record['visited']) == {r['id'] for r in record['spec']['risks']} and phases == {'transit', 'risk', 'exit'} and profile == 'lightweight' and not record['violations']:
                    record.update(state='succeeded', completed_at=now, reason='cloud_verified_route_protection')
        elif kind == 'mission.receipt':
            if body.get('workloads_reconciled') is not True:
                raise ProtocolError('mission phase requires old workload deactivation confirmation')
            if record['plan'] is None:
                raise ProtocolError('mission plan missing')
            phase = next((p for p in record['plan']['phases'] if p['stage'] == body['stage']), None)
            if phase is None:
                raise ProtocolError('unregistered mission phase')
            target = phase['target']
            if body['stage'] == 'transit' and body['request_id'] is None:
                binding = {'request_id': 'baseline-' + record['mission_id'] + ':' + str(record['revision']), 'epoch': header['epoch'], 'protection_profile': target['protection_profile']}
                if switch.state.active() != header['epoch']:
                    raise ProtocolError('baseline must use active authenticated epoch')
            else:
                change = switch.state.get(body['request_id'])
                binding = change['binding']
                if change['state'] != 'confirmed' or not change['confirmed_receipt'] or binding['epoch'] != header['epoch'] or switch.state.active() != header['epoch']:
                    raise ProtocolError('mission requires actual confirmed business receipt')
            actual = {'auth_policy': switch.state.policy(header['epoch']), 'suite': switch.state.suite(header['epoch']), 'protection_profile': switch.state.profile(header['epoch'])}
            if actual != target:
                raise ProtocolError('mission target differs from actual protection')
            if body['stage'] == 'exit':
                self.guard_switch(binding)
            session = auth.get(header['auth_session_id'])
            receipt = {**actual, 'stage': body['stage'], 'revision': record['revision'], 'epoch': header['epoch'],
                'request_id': binding['request_id'], 'session_expires': session['session_expires'], 'key_expires': session['key_expires'],
                'identity_verified': True, 'new_session_business_verified': True, 'workloads_reconciled': True, 'verified_at': self.clock()}
            if not any(r['revision'] == receipt['revision'] and r['request_id'] == receipt['request_id'] for r in record['receipts']):
                record['receipts'].append(receipt)
        else:
            raise ProtocolError('unsupported mission operation')
        return self.save(record)
