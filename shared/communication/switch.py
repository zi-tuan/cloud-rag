"""持久化双端切换协议；丢失提交应答后先对账，不猜测远端状态。"""
from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path

from shared.communication.database import SQLiteDatabase
from shared.communication.envelope import IDENTIFIER, ProtocolError, SUITES, canonical
from shared.communication.auth_protocol import POLICIES


class SwitchState:
    """一个设备只有一个未完成切换；epoch 永不复用。"""
    def __init__(self, path, codec, initial_epoch, initial_suite, *, initialize=False, clock=time.time, initial_profile='lightweight'):
        if not Path(path).is_file() and not initialize:
            raise ProtocolError('switch state missing; restore before starting')
        self.codec, self.clock = codec, clock
        self.lock = threading.RLock()
        self.db = SQLiteDatabase(path)
        with self.db.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS switch_identity (id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL, active TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS switch_epochs (epoch TEXT PRIMARY KEY, suite TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS switch_auth_policies (epoch TEXT PRIMARY KEY, policy TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS switch_profiles (epoch TEXT PRIMARY KEY, profile TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS switches (request_id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS switch_retirements (epoch TEXT PRIMARY KEY, suite TEXT NOT NULL, retire_at REAL NOT NULL, scheduled INTEGER NOT NULL)')
            identity = canonical([codec.device_id, codec.key_id, codec.role]).decode()
            row = db.execute('SELECT identity FROM switch_identity WHERE id=1').fetchone()
            if row is None:
                if not initialize or initial_suite not in SUITES or not IDENTIFIER.fullmatch(initial_epoch):
                    raise ProtocolError('explicit initial session required')
                db.execute('INSERT INTO switch_identity VALUES (1,?,?)', (identity, initial_epoch))
                db.execute('INSERT INTO switch_epochs VALUES (?,?)', (initial_epoch, initial_suite))
            elif row[0] != identity:
                raise ProtocolError('switch identity mismatch')
            db.execute("INSERT OR IGNORE INTO switch_auth_policies SELECT epoch,'Ed25519' FROM switch_epochs")
            from shared.communication.protection import protection_policy
            protection_policy(initial_profile)
            db.execute('INSERT OR IGNORE INTO switch_profiles SELECT epoch,? FROM switch_epochs', (initial_profile,))
        self.sync_codec()

    def active(self):
        with self.db.connect() as db:
            return db.execute('SELECT active FROM switch_identity WHERE id=1').fetchone()[0]

    def suite(self, epoch):
        with self.db.connect() as db:
            row = db.execute('SELECT suite FROM switch_epochs WHERE epoch=?', (epoch,)).fetchone()
        if row is None:
            raise ProtocolError('unknown switch epoch')
        return row[0]

    def policy(self, epoch):
        with self.db.connect() as db:
            row = db.execute('SELECT policy FROM switch_auth_policies WHERE epoch=?', (epoch,)).fetchone()
        if row is None:
            raise ProtocolError('unknown authentication epoch')
        return row[0]

    def get(self, request_id):
        with self.db.connect() as db:
            row = db.execute('SELECT data FROM switches WHERE request_id=?', (request_id,)).fetchone()
        if row is None:
            raise ProtocolError('unknown switch request')
        return json.loads(row[0])

    def profile(self, epoch):
        with self.db.connect() as db:
            row = db.execute('SELECT profile FROM switch_profiles WHERE epoch=?', (epoch,)).fetchone()
        if row is None:
            raise ProtocolError('unknown protection epoch')
        return row[0]

    def latest(self):
        with self.db.connect() as db:
            row = db.execute('SELECT data FROM switches ORDER BY rowid DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None

    def begin(self, binding, *, timeout=30, grace=30, recovery=False):
        required = {'device_id', 'request_id', 'old_epoch', 'epoch', 'suite', 'protection_profile'}
        if not isinstance(binding, dict) or set(binding) not in (required, required | {'auth_policy'}):
            raise ProtocolError('invalid switch binding')
        if any(not isinstance(value, str) or not IDENTIFIER.fullmatch(value) for value in binding.values()):
            raise ProtocolError('invalid switch identifier')
        if binding['device_id'] != self.codec.device_id or binding['suite'] not in SUITES:
            raise ProtocolError('invalid switch identity or suite')
        policy = binding.get('auth_policy', self.policy(binding['old_epoch']))
        from shared.communication.protection import protection_policy
        protection_policy(binding['protection_profile'])
        if not 0 <= grace <= 30:
            raise ProtocolError('transition exceeds registered maximum')
        if policy not in POLICIES:
            raise ProtocolError('invalid target authentication policy')
        with self.lock, self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT data FROM switches WHERE request_id=?', (binding['request_id'],)).fetchone()
            if existing:
                record = json.loads(existing[0])
                if record['binding'] != binding:
                    raise ProtocolError('switch identifier conflict')
                return record
            latest = db.execute('SELECT data FROM switches ORDER BY rowid DESC LIMIT 1').fetchone()
            if latest:
                last = json.loads(latest[0])
                if last['state'] not in ('confirmed', 'aborted') or self.clock() < last.get('retire_at', float('inf')):
                    raise ProtocolError('previous switch still in flight or grace period')
            active = db.execute('SELECT active FROM switch_identity WHERE id=1').fetchone()[0]
            if active != binding['old_epoch'] or binding['epoch'] == active:
                raise ProtocolError('old epoch mismatch')
            if db.execute('SELECT 1 FROM switch_epochs WHERE epoch=?', (binding['epoch'],)).fetchone():
                raise ProtocolError('epoch reuse forbidden')
            record = {'binding': binding, 'state': 'prepared', 'deadline': self.clock() + timeout,
                      'grace': grace, 'cloud_nonce': secrets.token_hex(32), 'edge_nonce': None,
                      'business_receipts': [], 'confirmed_receipt': None}
            if recovery:
                record['recovery'] = True
            db.execute('INSERT INTO switch_epochs VALUES (?,?)', (binding['epoch'], binding['suite']))
            db.execute('INSERT INTO switch_auth_policies VALUES (?,?)', (binding['epoch'], policy))
            db.execute('INSERT INTO switch_profiles VALUES (?,?)', (binding['epoch'], binding['protection_profile']))
            db.execute('INSERT INTO switches VALUES (?,?)', (binding['request_id'], canonical(record).decode()))
        self.sync_codec()
        return record

    def save(self, record, *, active=None):
        with self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE switches SET data=? WHERE request_id=?',
                       (canonical(record).decode(), record['binding']['request_id']))
            if active is not None:
                db.execute('UPDATE switch_identity SET active=? WHERE id=1', (active,))
            if record['state'] in ('committed', 'confirmed', 'aborted'):
                binding = record['binding']
                epoch = binding['epoch'] if record['state'] == 'aborted' else binding['old_epoch']
                suite = db.execute('SELECT suite FROM switch_epochs WHERE epoch=?', (epoch,)).fetchone()[0]
                db.execute('INSERT OR IGNORE INTO switch_retirements VALUES (?,?,?,0)', (epoch, suite, record['retire_at']))
                cutoff = db.execute('SELECT retire_at FROM switch_retirements WHERE epoch=?', (epoch,)).fetchone()[0]
                if cutoff != record['retire_at']:
                    raise ProtocolError('retirement deadline cannot change')
        self.sync_codec()

    def sync_codec(self):
        """终态只在有界过渡期接受非当前会话；未对账的边端保留候选用于对账。"""
        with self.lock, self.db.connect() as db:
            # 切换与退役计划同一事务落盘；崩溃留下的未确认计划按原截止时间重发。
            jobs = db.execute('SELECT epoch,suite,retire_at FROM switch_retirements WHERE scheduled=0').fetchall()
            unavailable = set()
            for epoch, suite, retire_at in jobs:
                try:
                    self.codec.schedule_retirement(epoch, suite, retire_at)
                except OSError:
                    # 退役队列保持未确认。禁止本端使用该旧会话，但不阻断
                    # 已提交的新会话；恢复后重发原截止时间，绝不重新计时。
                    unavailable.add(epoch)
                    continue
                with self.db.connect() as writer:
                    writer.execute('UPDATE switch_retirements SET scheduled=1 WHERE epoch=?', (epoch,))
            active = db.execute('SELECT active FROM switch_identity WHERE id=1').fetchone()[0]
            allowed = {active}
            row = db.execute('SELECT data FROM switches ORDER BY rowid DESC LIMIT 1').fetchone()
            if row:
                record = json.loads(row[0])
                pending_edge = self.codec.role == 'edge' and record['state'] not in ('confirmed', 'aborted')
                preparing_cloud = record['state'] in ('prepared', 'probed')
                if pending_edge or preparing_cloud or self.clock() < record.get('retire_at', 0):
                    allowed.update((record['binding']['epoch'], record['binding']['old_epoch']))
            allowed.difference_update(unavailable)
            for epoch, suite in db.execute('SELECT epoch,suite FROM switch_epochs'):
                if epoch in unavailable:
                    self.codec.block_epoch(epoch)
                elif epoch in allowed:
                    self.codec.allow_epoch(epoch, suite)
                else:
                    try:
                        self.codec.retire_epoch(epoch)
                    except OSError:
                        # 客户端先封锁旧 epoch；远端不可达时保留映射待重试。
                        # 服务持久化的租约/退役期限继续独立生效。
                        pass


class CloudSwitch:
    def __init__(self, state, ready, auth_ready=None):
        self.state, self.ready = state, ready
        self.auth_ready = auth_ready or (lambda policy: policy == 'Ed25519')
        self.lock = state.lock
        self.authorize_binding = lambda binding: None
        self.epoch_expires = lambda epoch: float('inf')
        self.expired_deadline = lambda epoch: float('inf')

    def maintain(self):
        with self.lock:
            record = self.state.latest()
            if record and record['state'] in ('prepared', 'probed') and self.state.clock() >= record['deadline']:
                record.update(state='aborted', retire_at=self.state.clock() + record['grace'])
                self.state.save(record, active=record['binding']['old_epoch'])
            # 已提交但确认丢失时不自动回滚；保留当前会话并等待显式对账。
            self.state.sync_codec()

    def dispatch(self, header, payload):
        with self.lock:
            self.maintain()
            binding = payload.get('binding')
            if not isinstance(binding, dict):
                raise ProtocolError('missing switch binding')
            op = header['kind']
            if op == 'switch.prepare':
                if header['epoch'] != binding.get('old_epoch'):
                    raise ProtocolError('prepare must use old session')
                record = self.state.begin(binding)
                # 失败也持久化为终态，防止重复 prepare 稍后意外执行。
                if record['state'] == 'prepared':
                    try:
                        self.authorize_binding(binding)
                        permitted = self.target_ready(binding)
                    except ProtocolError:
                        permitted = False
                    if not permitted:
                        record.update(state='aborted', retire_at=self.state.clock())
                        self.state.save(record)
            else:
                record = self.state.get(binding.get('request_id'))
                if record['binding'] != binding:
                    raise ProtocolError('switch binding mismatch')
            b = record['binding']
            if header['device_id'] != b['device_id'] or header['epoch'] not in (b['old_epoch'], b['epoch']):
                raise ProtocolError('switch sender/session mismatch')
            if op == 'switch.probe':
                if header['epoch'] != b['epoch'] or record['state'] not in ('prepared', 'probed'):
                    raise ProtocolError('invalid probe state')
                nonce = payload.get('edge_nonce')
                if not isinstance(nonce, str) or len(nonce) != 64:
                    raise ProtocolError('invalid probe challenge')
                if record['edge_nonce'] is not None and record['edge_nonce'] != nonce:
                    raise ProtocolError('probe challenge conflict')
                record.update(state='probed', edge_nonce=nonce)
                self.state.save(record)
                return self.summary(record) | {'cloud_nonce': record['cloud_nonce'], 'edge_nonce': nonce}
            if op == 'switch.commit':
                recovery = record.get('recovery', False)
                commit_epoch = b['epoch'] if recovery else b['old_epoch']
                if header['epoch'] != commit_epoch or record['state'] not in ('probed', 'committed', 'confirmed'):
                    raise ProtocolError('invalid commit state')
                if recovery and self.state.clock() < self.expired_deadline(b['old_epoch']):
                    raise ProtocolError('recovery cannot replace an unexpired session')
                if payload.get('cloud_nonce') != record['cloud_nonce'] or payload.get('edge_nonce') != record['edge_nonce']:
                    raise ProtocolError('bidirectional probe proof missing')
                if record['state'] == 'probed':
                    try:
                        self.authorize_binding(b)
                        permitted = self.target_ready(b)
                    except ProtocolError:
                        permitted = False
                    if not permitted:
                        record.update(state='aborted', retire_at=self.state.clock())
                        self.state.save(record)
                        return self.summary(record)
                    retire_at = (self.expired_deadline(b['old_epoch']) if recovery else
                                 min(self.state.clock() + record['grace'], self.epoch_expires(b['old_epoch'])))
                    record.update(state='committed', retire_at=retire_at)
                    self.state.save(record, active=b['epoch'])
            elif op == 'switch.confirm':
                if header['epoch'] != b['epoch'] or record['state'] not in ('committed', 'confirmed'):
                    raise ProtocolError('invalid confirm state')
                receipt = payload.get('business_receipt')
                if not receipt or (receipt not in record['business_receipts'] and receipt != record['confirmed_receipt']):
                    raise ProtocolError('actual business decryption evidence missing')
                record.update(state='confirmed', confirmed_receipt=receipt)
                self.state.save(record)
            elif op == 'switch.abort':
                if record['state'] in ('committed', 'confirmed'):
                    raise ProtocolError('committed switch must reconcile; a new change requires a new epoch')
                if record['state'] != 'aborted':
                    record.update(state='aborted', retire_at=self.state.clock() + record['grace'])
                    self.state.save(record, active=b['old_epoch'])
            elif op not in ('switch.prepare', 'switch.commit', 'switch.status'):
                raise ProtocolError('unsupported switch operation')
            return self.summary(record)

    def summary(self, record):
        # 挑战不出现在 status/日志中，只有新会话 probe 应答携带 cloud_nonce。
        return {'binding': record['binding'], 'state': record['state'], 'active': self.state.active(),
                'retire_at': record.get('retire_at')}

    def target_ready(self, binding):
        return self.ready(binding['suite']) and self.auth_ready(self.state.policy(binding['epoch']))

    def accept_business(self, header):
        record = self.state.latest()
        if header['epoch'] == self.state.active():
            return
        if record and record['state'] in ('committed', 'confirmed') and header['epoch'] == record['binding']['old_epoch'] and self.state.clock() < record['retire_at']:
            return
        raise ProtocolError('business epoch is not active')

    def business_receipt(self, header):
        """只有成功处理的新会话业务才生成回执；对端回送可证明解密应答。"""
        record = self.state.latest()
        if not record or record['state'] != 'committed' or header['epoch'] != record['binding']['epoch']:
            return None
        receipt = secrets.token_hex(32)
        record['business_receipts'] = (record['business_receipts'] + [receipt])[-64:]
        self.state.save(record)
        return receipt
