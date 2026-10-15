"""操作员飞控队列及持久回执；每次指令只允许领取一次。"""
import json
import time
import uuid
from shared.communication.database import SQLiteDatabase
from shared.communication.envelope import ProtocolError, canonical
from shared.communication.flight import FlightRequest, validate_route


class FlightStore:
    def __init__(self, path, clock=time.time):
        self.db, self.clock = SQLiteDatabase(path), clock
        with self.db.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS flights (id TEXT PRIMARY KEY, device TEXT NOT NULL, request_id TEXT UNIQUE NOT NULL, data TEXT NOT NULL)')

    def records(self, device):
        with self.db.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM flights WHERE device=? ORDER BY rowid DESC LIMIT 50', (device,))]

    def save(self, record):
        with self.db.connect() as db:
            db.execute('UPDATE flights SET data=? WHERE id=?', (canonical(record).decode(), record['flight_id']))
        return record

    def submit(self, body, application):
        request = FlightRequest.model_validate(body).model_dump()
        device = request['device_id']
        if device != application.codec.device_id:
            raise ProtocolError('unknown flight device')
        with self.db.connect() as db:
            previous = db.execute('SELECT data FROM flights WHERE request_id=?', (request['request_id'],)).fetchone()
        if previous:
            record = json.loads(previous[0])
            if record['request'] != request:
                raise ProtocolError('flight_request_conflict')
            return record
        info = application.store.device(device)
        if not info['online']:
            raise ProtocolError('flight_device_offline')
        epoch = application.switch.state.active()
        if not application.auth.status(epoch)['authenticated']:
            raise ProtocolError('flight_identity_session_unavailable')
        telemetry = (info.get('status') or {}).get('telemetry') or {}
        message = (telemetry.get('messages') or {}).get('GLOBAL_POSITION_INT')
        if not telemetry.get('online') or not message:
            raise ProtocolError('flight_telemetry_stale')
        sampled_at = telemetry['sampled_at'] - message['age_seconds']
        if not 0 <= self.clock() - sampled_at <= 5:
            raise ProtocolError('flight_telemetry_stale')
        route = None
        if request['action'] == 'route':
            mission = application.missions.get(request['mission_id'], device)
            if mission['revision'] != request['revision']:
                raise ProtocolError('flight_route_revision_changed')
            fields = message['fields']
            route = validate_route(mission, {'position': {'lat': fields['lat']/1e7, 'lon': fields['lon']/1e7}, 'sampled_at': sampled_at}, application.switch.state.profile(epoch), self.clock())
        active = [r for r in self.records(device) if r['state'] in ('queued', 'claimed', 'running')]
        if active and request['action'] not in ('hold', 'land'):
            raise ProtocolError('flight_already_active')
        # 停留/降落在下一次领取时抢占航线；失联时不把提交当作已执行。
        if request['action'] in ('hold', 'land'):
            for record in active:
                record.update(state='interrupted', reason='operator_override')
                self.save(record)
        record = {'flight_id': uuid.uuid4().hex, 'device_id': device, 'request': request, 'route_spec': route,
                  'state': 'queued', 'created_at': self.clock(), 'events': [], 'simulation_only': True}
        with self.db.connect() as db:
            db.execute('INSERT INTO flights VALUES (?,?,?,?)', (record['flight_id'], device, request['request_id'], canonical(record).decode()))
        return record

    def dispatch(self, kind, device, body):
        records = self.records(device)
        if kind == 'flight.poll':
            queued = next((r for r in reversed(records) if r['state'] == 'queued'), None)
            return queued or next((r for r in records if r['state'] in ('claimed', 'running')), None)
        record = next((r for r in records if r['flight_id'] == body.get('flight_id')), None)
        if record is None:
            raise ProtocolError('unknown flight command')
        if kind == 'flight.claim':
            if record['state'] != 'queued':
                raise ProtocolError('flight_already_claimed_no_reexecution')
            if self.clock() - record['created_at'] > 60:
                record.update(state='failed', reason='flight_command_expired')
            else:
                record.update(state='claimed', claimed_at=self.clock())
            return self.save(record)
        if kind != 'flight.event' or body.get('state') not in ('running', 'completed', 'failed', 'interrupted'):
            raise ProtocolError('invalid flight event')
        if record['state'] in ('completed', 'failed', 'interrupted'):
            return record
        if record['state'] not in ('claimed', 'running'):
            raise ProtocolError('flight command was not claimed')
        record.update(state=body['state'], reason=body.get('reason'), evidence=body.get('evidence'), updated_at=self.clock())
        record['events'].append({'state': body['state'], 'reason': body.get('reason'), 'at': self.clock()})
        record['events'] = record['events'][-30:]
        return self.save(record)
