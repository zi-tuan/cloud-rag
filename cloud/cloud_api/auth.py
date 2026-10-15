"""云端持久化身份准入；挑战一次性消费，未确认会话不能访问业务。"""
from __future__ import annotations

import json
import secrets
import threading
import time

from shared.communication.auth_protocol import validate_transcript
from shared.communication.database import SQLiteDatabase
from shared.communication.envelope import IDENTIFIER, ProtocolError, canonical


class CloudAuth:
    def __init__(self, path, provider, codec, *, cloud_id, credential_version='1',
                 policy_for_epoch=None, profile_for_epoch=None, clock=time.time, challenge_ttl=60, session_ttl=900):
        if not 1 <= challenge_ttl < session_ttl <= 86400:
            raise ValueError('invalid authentication TTL')
        self.db, self.provider, self.codec = SQLiteDatabase(path), provider, codec
        self.cloud_id, self.credential_version = cloud_id, credential_version
        self.policy_for_epoch = policy_for_epoch or (lambda epoch: 'Ed25519')
        self.profile_for_epoch = profile_for_epoch or (lambda epoch: 'lightweight')
        self.clock, self.challenge_ttl, self.session_ttl = clock, challenge_ttl, session_ttl
        self.lock = threading.RLock()
        with self.db.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS auth_sessions (id TEXT PRIMARY KEY, request_id TEXT UNIQUE, transcript TEXT NOT NULL, state TEXT NOT NULL, receipt TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS epoch_deadlines (epoch TEXT PRIMARY KEY, profile TEXT NOT NULL, issued INTEGER NOT NULL, expires INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS authentication_events (session_id TEXT PRIMARY KEY, device TEXT NOT NULL, epoch TEXT NOT NULL, profile TEXT NOT NULL, policy TEXT NOT NULL, activated_at REAL NOT NULL)')
            # 启动时无法确认的中间状态一律重新认证；已激活会话由持久状态恢复。
            db.execute("UPDATE auth_sessions SET state='revoked' WHERE state != 'active'")

    def start(self, body):
        expected = {'device_id', 'policy', 'credential_version', 'key_id', 'epoch', 'suite', 'client_nonce', 'request_id', 'protection_profile'}
        if not isinstance(body, dict) or set(body) != expected:
            raise ProtocolError('invalid authentication start')
        if body['device_id'] != self.codec.device_id or body['key_id'] != self.codec.key_id:
            raise ProtocolError('unregistered device')
        epoch = body['epoch']
        if not isinstance(epoch, str) or not IDENTIFIER.fullmatch(epoch):
            raise ProtocolError('invalid epoch')
        epochs = getattr(self.codec, 'epochs', None)
        if epochs is None:
            epochs = self.codec._epochs
        if epochs.get(epoch) != body['suite']:
            raise ProtocolError('authentication epoch is not prepared')
        if body['policy'] != self.policy_for_epoch(epoch):
            raise ProtocolError('authentication policy is not prepared for epoch')
        if body['credential_version'] != self.credential_version:
            raise ProtocolError('unregistered credential version')
        if body['protection_profile'] != self.profile_for_epoch(epoch):
            raise ProtocolError('protection profile is not prepared for epoch')
        from shared.communication.protection import protection_policy
        profile = protection_policy(body['protection_profile'])
        now = int(self.clock())
        with self.lock, self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO epoch_deadlines VALUES (?,?,?,?)', (epoch, body['protection_profile'], now, now + profile['key_ttl']))
            row = db.execute('SELECT profile,expires FROM epoch_deadlines WHERE epoch=?', (epoch,)).fetchone()
        expires = min(now + profile['session_ttl'], row[1])
        if row[0] != body['protection_profile'] or expires <= now + 1:
            raise ProtocolError('business key expired; new epoch required')
        transcript = {**body, 'v': 1, 'cloud_id': self.cloud_id, 'policy_version': '1',
                      'server_nonce': secrets.token_hex(32), 'auth_session_id': secrets.token_hex(32),
                      'challenge_expires': min(now + self.challenge_ttl, expires - 1), 'session_expires': expires, 'key_expires': row[1]}
        validate_transcript(transcript)
        if hasattr(self.codec, 'lease'):
            self.codec.lease(epoch, body['protection_profile'], row[1])
        proof = self.provider.prove('cloud-challenge', transcript)
        with self.lock, self.db.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM auth_sessions WHERE json_extract(transcript, \'$.session_expires\') <= ?', (now,))
            # 单设备最多八个有效中间会话，限制匿名挑战的资源占用。
            rows = db.execute("SELECT transcript FROM auth_sessions WHERE state IN ('challenged','verifying','pending','confirmed')").fetchall()
            if sum(json.loads(row[0])['challenge_expires'] > now for row in rows) >= 8:
                raise ProtocolError('too many pending authentications')
            if db.execute('SELECT 1 FROM auth_sessions WHERE request_id=?', (body['request_id'],)).fetchone():
                raise ProtocolError('authentication request replay')
            db.execute('INSERT INTO auth_sessions VALUES (?,?,?,?,NULL)',
                       (transcript['auth_session_id'], body['request_id'], canonical(transcript).decode(), 'challenged'))
        return {'transcript': transcript, 'proof': proof}

    def _record(self, session_id):
        if not isinstance(session_id, str) or not IDENTIFIER.fullmatch(session_id):
            raise ProtocolError('invalid authentication session identifier')
        with self.db.connect() as db:
            row = db.execute('SELECT transcript,state,receipt FROM auth_sessions WHERE id=?', (session_id,)).fetchone()
        if row is None:
            raise ProtocolError('unknown authentication session')
        transcript = json.loads(row[0])
        if row[1] == 'revoked' or self.clock() >= transcript['session_expires']:
            raise ProtocolError('authentication session expired or revoked')
        if row[1] != 'active' and self.clock() >= transcript['challenge_expires']:
            raise ProtocolError('authentication confirmation expired')
        return transcript, row[1], row[2]

    def respond(self, body):
        if not isinstance(body, dict) or set(body) != {'auth_session_id', 'proof'}:
            raise ProtocolError('invalid authentication response')
        with self.lock:
            transcript, state, _ = self._record(body['auth_session_id'])
            if state != 'challenged':
                raise ProtocolError('challenge already consumed')
            # 消费先持久化，验签失败也不能重放；崩溃留下的 verifying 重启时作废。
            with self.db.connect() as db:
                updated = db.execute("UPDATE auth_sessions SET state='verifying' WHERE id=? AND state='challenged'", (body['auth_session_id'],))
                if updated.rowcount != 1:
                    raise ProtocolError('challenge already consumed')
            self.provider.verify('edge-response', transcript, body['proof'])
            with self.db.connect() as db:
                db.execute("UPDATE auth_sessions SET state='pending' WHERE id=?", (body['auth_session_id'],))
        return {'auth_session_id': transcript['auth_session_id'], 'state': 'pending'}

    def get(self, session_id):
        transcript, state, _ = self._record(session_id)
        if state not in ('pending', 'confirmed', 'active'):
            raise ProtocolError('identity verification incomplete')
        return transcript

    def for_epoch(self, epoch):
        with self.db.connect() as db:
            rows = db.execute("SELECT id FROM auth_sessions WHERE state='active' ORDER BY rowid DESC").fetchall()
        for row in rows:
            try:
                transcript = self.get(row[0])
            except ProtocolError:
                continue
            if transcript['epoch'] == epoch:
                return transcript
        raise ProtocolError('no authenticated epoch')

    def admit(self, header):
        _, state, _ = self._record(header['auth_session_id'])
        if header['kind'] in ('auth.confirm', 'auth.activate'):
            return
        if state != 'active':
            raise ProtocolError('authentication confirmation incomplete')

    def confirm(self, header, body):
        with self.lock:
            transcript, state, receipt = self._record(header['auth_session_id'])
            if header['kind'] == 'auth.confirm':
                if body != {'server_nonce': transcript['server_nonce']} or state not in ('pending', 'confirmed'):
                    raise ProtocolError('invalid encrypted authentication confirmation')
                receipt = receipt or secrets.token_hex(32)
                with self.db.connect() as db:
                    db.execute("UPDATE auth_sessions SET state='confirmed',receipt=? WHERE id=?", (receipt, header['auth_session_id']))
                return {'state': 'confirmed', 'receipt': receipt}
            if header['kind'] != 'auth.activate' or state not in ('confirmed', 'active') or body != {'receipt': receipt}:
                raise ProtocolError('invalid authentication activation')
            with self.db.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE auth_sessions SET state='active' WHERE id=?", (header['auth_session_id'],))
                db.execute('INSERT OR IGNORE INTO authentication_events VALUES (?,?,?,?,?,?)',
                    (header['auth_session_id'], transcript['device_id'], transcript['epoch'],
                     transcript['protection_profile'], transcript['policy'], self.clock()))
            return {'state': 'active', 'policy': transcript['policy'], 'expires_at': transcript['session_expires']}

    def revoke(self, session_id):
        with self.lock, self.db.connect() as db:
            db.execute("UPDATE auth_sessions SET state='revoked' WHERE id=?", (session_id,))
        return {'revoked': True}

    def deadline(self, epoch):
        with self.db.connect() as db:
            row = db.execute('SELECT expires FROM epoch_deadlines WHERE epoch=?', (epoch,)).fetchone()
        if row is None:
            raise ProtocolError('epoch has no established lifetime')
        return row[0]

    def status(self, epoch=None):
        with self.db.connect() as db:
            rows = db.execute("SELECT transcript FROM auth_sessions WHERE state='active' ORDER BY rowid DESC").fetchall()
        now = self.clock()
        active = [json.loads(row[0]) for row in rows if json.loads(row[0])['session_expires'] > now
                  and (epoch is None or json.loads(row[0])['epoch'] == epoch)]
        if not active:
            return {'authenticated': False, 'policy': 'Ed25519'}
        value = active[0]
        return {'authenticated': True, 'policy': value['policy'], 'policy_version': value['policy_version'],
                'credential_version': value['credential_version'], 'expires_at': value['session_expires'],
                'epoch': value['epoch'], 'suite': value['suite'], 'protection_profile': value['protection_profile'],
                'key_expires': value['key_expires'], 'bidirectional_confirmation': True}
