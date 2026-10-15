"""操作员授权的过期会话恢复；候选仍必须完成双向认证和切换回执。"""
import uuid
import secrets
import hashlib
from shared.communication.envelope import IDENTIFIER, ProtocolError
from shared.communication.envelope import canonical


class SessionRecovery:
    def __init__(self, application):
        self.app = application

    def request(self, body):
        app = self.app
        if set(body) != {'device_id', 'request_id', 'expected_epoch'}:
            raise ProtocolError('invalid recovery request')
        if any(not isinstance(v, str) or not IDENTIFIER.fullmatch(v) for v in body.values()):
            raise ProtocolError('invalid recovery identifier')
        state = app.switch.state
        if body['device_id'] != state.codec.device_id:
            raise ProtocolError('unregistered recovery device')
        request = 'recover-' + uuid.uuid5(uuid.NAMESPACE_URL, body['request_id']).hex
        try:
            existing = state.get(request)
        except ProtocolError:
            existing = None
        if existing:
            if not existing.get('recovery') or existing['binding']['old_epoch'] != body['expected_epoch']:
                raise ProtocolError('recovery request conflict')
            return self.status()
        old = state.active()
        if old != body['expected_epoch']:
            raise ProtocolError('current epoch changed; refresh before recovery')
        expiry = app.auth.deadline(old)
        if app.auth.clock() < expiry:
            raise ProtocolError('current session has not expired')
        app.switch.maintain()
        latest = state.latest()
        if latest and (latest.get('uncertain') or latest['state'] not in ('confirmed', 'aborted')):
            raise ProtocolError('reconcile existing switch before recovery')
        binding = {'device_id': body['device_id'], 'request_id': request, 'old_epoch': old,
                   'epoch': 'recovery-' + uuid.uuid5(uuid.NAMESPACE_URL, request).hex,
                   'suite': state.suite(old), 'auth_policy': state.policy(old),
                   'protection_profile': state.profile(old)}
        # 与普通事务同一持久记录，标记与创建原子落盘。保持原等级，禁止借恢复降级。
        state.begin(binding, timeout=120, grace=0, recovery=True)
        return self.status()

    def offer(self, body):
        if set(body) != {'device_id', 'old_epoch', 'nonce'} or body['device_id'] != self.app.codec.device_id:
            raise ProtocolError('invalid recovery discovery')
        nonce = body['nonce']
        if not isinstance(nonce, str) or len(nonce) != 64 or any(c not in '0123456789abcdef' for c in nonce):
            raise ProtocolError('invalid recovery discovery nonce')
        record = self.app.switch.state.latest()
        if not record or not record.get('recovery') or record['binding']['old_epoch'] != body['old_epoch']:
            return {'binding': None}
        if record['state'] == 'aborted':
            return {'binding': None}
        binding = record['binding']
        now = int(self.app.auth.clock())
        # 专用请求域绑定完整事务和本次随机数。这份签名不是可激活的认证会话。
        transcript = {'v': 1, 'device_id': binding['device_id'], 'cloud_id': self.app.auth.cloud_id,
            'policy': binding['auth_policy'], 'credential_version': self.app.auth.credential_version,
            'policy_version': '1', 'key_id': self.app.codec.key_id, 'epoch': binding['epoch'],
            'suite': binding['suite'], 'protection_profile': binding['protection_profile'],
            'client_nonce': nonce, 'server_nonce': secrets.token_hex(32), 'auth_session_id': secrets.token_hex(32),
            'request_id': 'recovery-discovery-' + hashlib.sha256(canonical(binding)).hexdigest(),
            'challenge_expires': now + 30, 'session_expires': now + 31, 'key_expires': now + 31}
        return {'binding': binding, 'transcript': transcript,
                'proof': self.app.auth.provider.prove('cloud-challenge', transcript)}

    def status(self):
        record = self.app.switch.state.latest()
        if not record or not record.get('recovery'):
            return None
        labels = {'prepared': 'waiting_device', 'probed': 'authenticating',
                  'committed': 'verifying', 'confirmed': 'succeeded', 'aborted': 'failed'}
        return {'request_id': record['binding']['request_id'], 'state': labels[record['state']],
                'epoch': record['binding']['epoch'], 'old_epoch': record['binding']['old_epoch'],
                'business_verified': bool(record.get('confirmed_receipt'))}
