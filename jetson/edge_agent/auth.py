"""边端双向认证和到期续期；启动后重新认证，不保存私钥或签名原文。"""
from __future__ import annotations

import secrets
import threading
import time
import uuid
import json

from shared.communication.auth_client import AuthenticatedCodec
from shared.communication.auth_protocol import validate_transcript
from shared.communication.envelope import ProtocolError
from jetson.edge_agent.client import EdgeClient


class EdgeAuth:
    def __init__(self, codec, provider, *, cloud_id, credential_version='1', policy_for_epoch=None, profile_for_epoch=None, state_path=None, clock=time.time):
        self.codec, self.provider = codec, provider
        self.cloud_id, self.credential_version, self.clock = cloud_id, credential_version, clock
        self.sessions, self.active = {}, {}
        self.policy_for_epoch = policy_for_epoch or (lambda epoch: 'Ed25519')
        self.profile_for_epoch = profile_for_epoch or (lambda epoch: 'lightweight')
        self.lock = threading.RLock()
        self.db = None
        if state_path:
            from shared.communication.database import SQLiteDatabase
            self.db = SQLiteDatabase(state_path)
            with self.db.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS edge_auth_epochs (epoch TEXT PRIMARY KEY, transcript TEXT NOT NULL)')
                for epoch, value in db.execute('SELECT epoch,transcript FROM edge_auth_epochs'):
                    transcript = validate_transcript(json.loads(value))
                    self.sessions[transcript['auth_session_id']] = transcript
                    self.active[epoch] = transcript['auth_session_id']

    def get(self, session_id):
        value = self.sessions.get(session_id)
        if value is None or self.clock() >= value['session_expires']:
            raise ProtocolError('edge authentication session expired')
        return value

    def for_epoch(self, epoch):
        session_id = self.active.get(epoch)
        if session_id is None:
            raise ProtocolError('edge epoch is not authenticated')
        return self.get(session_id)

    def admit(self, header):
        if header['kind'] != 'business.reply':
            raise ProtocolError('unsupported cloud authentication message')

    def ensure(self, client, epoch):
        if epoch in self.active:
            self.for_epoch(epoch)
            return
        epochs = getattr(self.codec, 'epochs', None)
        if epochs is None:
            epochs = self.codec._epochs
        body = {'device_id': self.codec.device_id, 'policy': self.policy_for_epoch(epoch),
                'credential_version': self.credential_version, 'key_id': self.codec.key_id,
                'epoch': epoch, 'suite': epochs[epoch], 'protection_profile': self.profile_for_epoch(epoch), 'client_nonce': secrets.token_hex(32),
                'request_id': uuid.uuid4().hex}
        answer = client.auth_request('start', body)
        transcript = validate_transcript(answer['transcript'])
        if any(transcript[k] != v for k, v in body.items()) or transcript['cloud_id'] != self.cloud_id:
            raise ProtocolError('cloud authentication context mismatch')
        now = self.clock()
        if not now < transcript['challenge_expires'] <= now + 120 or not transcript['challenge_expires'] < transcript['session_expires'] <= now + 86400:
            raise ProtocolError('cloud authentication lifetime rejected')
        self.provider.verify('cloud-challenge', transcript, answer['proof'])
        if hasattr(self.codec, 'lease'):
            self.codec.lease(epoch, transcript['protection_profile'], transcript['key_expires'])
        session_id = transcript['auth_session_id']
        proof = self.provider.prove('edge-response', transcript)
        response = client.auth_request('respond', {'auth_session_id': session_id, 'proof': proof})
        if response != {'auth_session_id': session_id, 'state': 'pending'}:
            raise ProtocolError('cloud identity response mismatch')
        self.sessions[session_id] = transcript
        previous = self.active.get(epoch)
        self.active[epoch] = session_id
        try:
            result = EdgeClient.exchange_at(client, 'auth.confirm', {'server_nonce': transcript['server_nonce']}, epoch)['result']
            if result.get('state') != 'confirmed' or not isinstance(result.get('receipt'), str):
                raise ProtocolError('cloud encrypted confirmation mismatch')
            result = EdgeClient.exchange_at(client, 'auth.activate', {'receipt': result['receipt']}, epoch)['result']
            if result != {'state': 'active', 'policy': transcript['policy'], 'expires_at': transcript['session_expires']}:
                raise ProtocolError('cloud activation mismatch')
        except Exception:
            self.active.pop(epoch, None)
            self.sessions.pop(session_id, None)
            # 失败不无限续用旧会话；下次调用重做完整认证。
            raise
        if previous:
            self.sessions.pop(previous, None)
        if self.db:
            from shared.communication.envelope import canonical
            with self.db.connect() as db:
                db.execute('INSERT OR REPLACE INTO edge_auth_epochs VALUES (?,?)', (epoch, canonical(transcript).decode()))


class AuthenticatedEdgeClient(EdgeClient):
    def __init__(self, url, codec, current_epoch, auth, *, auth_transport=None, **kwargs):
        self.auth = auth
        super().__init__(url, AuthenticatedCodec(codec, auth.provider, auth), current_epoch, **kwargs)
        self.lock = auth.lock
        self.auth_transport = auth_transport

    def auth_request(self, operation, body):
        if self.auth_transport:
            return self.auth_transport(operation, body)
        url = self.url.rsplit('/api/edge/exchange', 1)
        if len(url) != 2 or url[1]:
            raise ProtocolError('authentication requires the configured exchange path')
        return self._post_url(url[0] + '/api/auth/' + operation, body)

    def exchange_at(self, kind, payload, epoch):
        with self.auth.lock:
            self.auth.ensure(self, epoch)
            return super().exchange_at(kind, payload, epoch)
