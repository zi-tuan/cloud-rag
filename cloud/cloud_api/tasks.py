"""持久化任务与设备连接状态；断连不触发任务重跑。"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from shared.communication.envelope import IDENTIFIER, ProtocolError, canonical


class TaskConflict(ProtocolError):
    pass


class TaskStore:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                    request_id TEXT NOT NULL, scenario TEXT NOT NULL,
                    state TEXT NOT NULL, result TEXT, created REAL NOT NULL,
                    UNIQUE(device_id, request_id));
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_task
                    ON tasks(device_id) WHERE state IN ('running','verifying','unknown');
                CREATE TABLE IF NOT EXISTS events (
                    task_id TEXT NOT NULL, event_id TEXT NOT NULL,
                    content TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(task_id,event_id));
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY, last_seen REAL NOT NULL,
                    status TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def identifier(value):
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            raise ProtocolError('invalid identifier')
        return value

    @staticmethod
    def record(row):
        if row is None:
            return None
        result = dict(row)
        result['result'] = json.loads(result['result']) if result['result'] else None
        return result

    def submit(self, device_id, request_id, scenario):
        self.identifier(device_id)
        self.identifier(request_id)
        if not isinstance(scenario, str) or not scenario.strip() or len(scenario) > 16000:
            raise ProtocolError('invalid scenario')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM tasks WHERE device_id=? AND request_id=?',
                             (device_id, request_id)).fetchone()
            if row:
                if row['scenario'] != scenario:
                    raise TaskConflict('request identifier already used for another scenario')
                return self.record(row)
            task_id = uuid.uuid4().hex
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)',
                       (task_id, device_id, request_id, scenario, 'queued', None, time.time()))
            return self.record(db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone())

    def get(self, task_id, device_id=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            if row is None or (device_id is not None and row['device_id'] != device_id):
                raise KeyError('task not found')
            return self.record(row)

    def claim(self, device_id):
        self.identifier(device_id)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute("SELECT * FROM tasks WHERE device_id=? AND state IN ('running','verifying','unknown')",
                                (device_id,)).fetchone()
            if active:
                # 重启或领取应答丢失后只对账，不把任务当作新任务执行。
                return {'task': self.record(active), 'reconcile_required': True}
            row = db.execute("SELECT * FROM tasks WHERE device_id=? AND state='queued' ORDER BY created,task_id LIMIT 1",
                             (device_id,)).fetchone()
            if row is None:
                return {'task': None, 'reconcile_required': False}
            db.execute("UPDATE tasks SET state='running' WHERE task_id=?", (row['task_id'],))
            task = self.record(row)
            task['state'] = 'running'
            return {'task': task, 'reconcile_required': False}

    def recent(self, device_id, limit=50):
        self.identifier(device_id)
        with self.connect() as db:
            return [self.record(row) for row in db.execute(
                'SELECT * FROM tasks WHERE device_id=? ORDER BY created DESC,task_id DESC LIMIT ?',
                (device_id, min(max(int(limit), 1), 100)))]

    def history(self, task_id, device_id):
        self.get(task_id, device_id)
        with self.connect() as db:
            return [{'event_id': row['event_id'], 'created': row['created'],
                     **json.loads(row['content'])} for row in db.execute(
                         'SELECT * FROM events WHERE task_id=? ORDER BY created,event_id', (task_id,))]

    def event(self, device_id, task_id, event_id, state, result=None):
        """终态成功必须由确定性验证入口调用，不能直接信任模型输出。"""
        self.identifier(event_id)
        transitions = {
            'running': {'running', 'verifying', 'failed', 'unknown'},
            'verifying': {'verifying', 'failed', 'unknown'},
            'unknown': {'unknown', 'verifying', 'failed'},
        }
        content = canonical({'state': state, 'result': result}).decode()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM tasks WHERE task_id=? AND device_id=?', (task_id, device_id)).fetchone()
            if row is None:
                raise KeyError('task not found')
            previous = db.execute('SELECT content FROM events WHERE task_id=? AND event_id=?', (task_id, event_id)).fetchone()
            if previous:
                if previous['content'] != content:
                    raise TaskConflict('event identifier conflict')
                return self.record(row)
            if state not in transitions.get(row['state'], set()):
                raise TaskConflict('invalid task transition')
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (task_id, event_id, content, time.time()))
            db.execute('UPDATE tasks SET state=?,result=? WHERE task_id=?',
                       (state, canonical(result).decode(), task_id))
            return self.record(db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone())

    def heartbeat(self, device_id, status, now=None):
        self.identifier(device_id)
        encoded = canonical(status).decode()
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO devices VALUES (?,?,?)',
                       (device_id, time.time() if now is None else now, encoded))

    def telemetry(self, device_id, snapshot, now=None):
        """真实遥测到达时立即更新设备；不等待下一次心跳转发同一位置。"""
        self.identifier(device_id)
        now = time.time() if now is None else now
        sampled = snapshot.get('sampled_at')
        if not isinstance(sampled, (int, float)) or not 0 <= now - sampled <= 5:
            return False
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT status FROM devices WHERE device_id=?', (device_id,)).fetchone()
            status = json.loads(previous['status']) if previous else {}
            last = (status.get('telemetry') or {}).get('sampled_at', 0)
            if sampled < last:
                return False
            status['telemetry'] = snapshot
            db.execute('INSERT OR REPLACE INTO devices VALUES (?,?,?)', (device_id, now, canonical(status).decode()))
        return True

    def verified_success(self, device_id, task_id, request_id, evidence):
        """仅供服务端确定性验证器调用，HTTP 事件接口不能直接访问。"""
        encoded = canonical(evidence).decode()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM tasks WHERE task_id=? AND device_id=?', (task_id, device_id)).fetchone()
            if row is None:
                raise KeyError('task not found')
            if row['request_id'] != request_id:
                raise TaskConflict('task and switch request mismatch')
            if row['state'] == 'succeeded' and row['result'] == encoded:
                return self.record(row)
            if row['state'] != 'verifying':
                raise TaskConflict('task must be verifying')
            db.execute("UPDATE tasks SET state='succeeded',result=? WHERE task_id=?", (encoded, task_id))
            return self.record(db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone())

    def device(self, device_id, timeout=20, now=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM devices WHERE device_id=?', (device_id,)).fetchone()
        if row is None:
            return {'device_id': device_id, 'online': False, 'status': None}
        return {'device_id': device_id, 'online': (time.time() if now is None else now) - row['last_seen'] <= timeout,
                'last_seen': row['last_seen'], 'status': json.loads(row['status'])}
