"""密码服务客户端；业务进程与模型均不持有设备根密钥。"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from shared.communication.envelope import IDENTIFIER, MAX_MESSAGE, ProtocolError, SUITES, canonical


class CryptoServiceCodec:
    def __init__(self, *, device_id, key_id, role, services, token, timeout=10):
        if role not in ('cloud', 'edge') or not all(IDENTIFIER.fullmatch(x) for x in (device_id, key_id)):
            raise ProtocolError('invalid codec identity')
        if set(services) != set(SUITES) or len(token) < 32:
            raise ProtocolError('both crypto services and a service credential are required')
        for url in services.values():
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ProtocolError('invalid internal crypto endpoint')
        self.device_id, self.key_id, self.role = device_id, key_id, role
        self.services, self.token, self.timeout = services, token, timeout
        self.epochs = {}
        self._prepared = set()
        self._retiring = set()
        self._prepare_lock = threading.RLock()

    def request(self, suite, path, body=None):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        request = urllib.request.Request(self.services[suite].rstrip('/') + path,
                                         None if body is None else canonical(body),
                                         {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        # 内部调用不继承桌面/系统代理，防止服务令牌送往外部代理。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=self.timeout) as response:
            data = response.read(MAX_MESSAGE * 2 + 1)
            if len(data) > MAX_MESSAGE * 2:
                raise ProtocolError('oversized crypto response')
            return json.loads(data)

    def ready(self, suite):
        if suite not in self.services:
            return False
        try:
            result = self.request(suite, '/ready')
            return result.get('ready') is True and result.get('suite') == suite
        except (OSError, ValueError, AttributeError):
            return False

    def allow_epoch(self, epoch, suite):
        if not isinstance(epoch, str) or not IDENTIFIER.fullmatch(epoch) or suite not in SUITES:
            raise ProtocolError('invalid epoch')
        with self._prepare_lock:
            if epoch in self.epochs and self.epochs[epoch] != suite:
                raise ProtocolError('epoch suite conflict')
            self.epochs[epoch] = suite

    def block_epoch(self, epoch):
        with self._prepare_lock:
            self._retiring.add(epoch)

    def retire_epoch(self, epoch):
        # 本地先禁止使用，服务确认持久化退役后才移除映射；失败可重试。
        with self._prepare_lock:
            suite = self.epochs.get(epoch)
            if suite is None:
                return
            self._retiring.add(epoch)
            if self.request(suite, '/v1/sessions/retire', {'epoch': epoch}) != {'retired': True}:
                raise ProtocolError('crypto service retirement was not confirmed')
            self.epochs.pop(epoch, None)
            self._prepared.discard(epoch)
            self._retiring.discard(epoch)

    def lease(self, epoch, protection_profile, expires_at):
        suite = self.epochs.get(epoch)
        if suite is None:
            raise ProtocolError('cannot lease unknown epoch')
        body = {'epoch': epoch, 'protection_profile': protection_profile, 'expires_at': expires_at}
        if self.request(suite, '/v1/sessions/lease', body) != {'leased': True, **body}:
            raise ProtocolError('crypto lease binding mismatch')

    def schedule_retirement(self, epoch, suite, retire_at):
        body = {'epoch': epoch, 'retire_at': retire_at}
        if self.request(suite, '/v1/sessions/retirement', body) != {'scheduled': True, **body, 'suite': suite}:
            raise ProtocolError('crypto retirement deadline binding mismatch')

    def session(self, epoch):
        with self._prepare_lock:
            suite = self.epochs.get(epoch)
            if suite is None or epoch in self._retiring:
                raise ProtocolError('unknown or retired epoch')
            if epoch not in self._prepared:
                result = self.request(suite, '/v1/sessions/prepare', {'epoch': epoch})
                if result != {'epoch': epoch, 'suite': suite, 'prepared': True}:
                    raise ProtocolError('crypto service preparation mismatch')
                self._prepared.add(epoch)
            # 这里只复用已持久化的准备结果。每次 seal/open 仍由密码服务校验
            # 不可延长的租约和退役标记，不能从客户端缓存推断密钥仍有效。
            return suite

    def seal(self, kind, payload, *, epoch, reply_to=None, auth_session_id=None):
        suite = self.session(epoch)
        body = {'kind': kind, 'payload': payload, 'epoch': epoch, 'reply_to': reply_to}
        if auth_session_id is not None:
            body['auth_session_id'] = auth_session_id
        result = self.request(suite, '/v1/seal', body)
        header = result.get('header', {})
        direction = 'edge-to-cloud' if self.role == 'edge' else 'cloud-to-edge'
        expected = {'device_id': self.device_id, 'key_id': self.key_id, 'epoch': epoch, 'suite': suite,
                    'direction': direction, 'kind': kind, 'reply_to': reply_to}
        if auth_session_id is not None:
            expected.update(v=2, auth_session_id=auth_session_id)
        if any(header.get(k) != v for k, v in expected.items()):
            raise ProtocolError('crypto service identity/session mismatch')
        return result

    def open(self, envelope):
        if not isinstance(envelope, dict) or not isinstance(envelope.get('header'), dict):
            raise ProtocolError('invalid envelope')
        header = envelope['header']
        epoch = header.get('epoch')
        if not isinstance(epoch, str):
            raise ProtocolError('invalid epoch')
        try:
            suite = self.session(epoch)
            result = self.request(suite, '/v1/open', {'envelope': envelope})
        except urllib.error.HTTPError as rejected:
            if rejected.code != 400:
                raise
            rejected.close()
            raise ProtocolError('crypto service rejected the incoming message') from rejected
        authenticated = result.get('header')
        direction = 'cloud-to-edge' if self.role == 'edge' else 'edge-to-cloud'
        if authenticated != header or header.get('device_id') != self.device_id or header.get('key_id') != self.key_id or header.get('direction') != direction or header.get('suite') != suite:
            raise ProtocolError('crypto service authenticated another identity')
        return authenticated, result['payload']
