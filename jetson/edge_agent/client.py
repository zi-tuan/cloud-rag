"""确定性加密传输；密钥不交给 Pi，传输失败不重跑业务。"""
from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request

from shared.communication.envelope import MAX_MESSAGE, ProtocolError, canonical


class BusinessRejected(ProtocolError):
    pass


class EdgeClient:
    def __init__(self, url, codec, current_epoch, *, timeout=15, transport=None):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'http' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('cloud endpoint must be an HTTP URL without embedded credentials')
        self.url, self.codec = url, codec
        self.current_epoch = current_epoch
        self.timeout = timeout
        self.transport = transport or self._post
        self.lock = threading.RLock()

    def _post(self, envelope):
        return self._post_url(self.url, envelope)

    def _post_url(self, url, envelope):
        request = urllib.request.Request(url, canonical(envelope), {'Content-Type': 'application/json'})
        # 禁止重定向，以免凭据/密文被转发至未配置端点。
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=self.timeout) as response:
            data = response.read(MAX_MESSAGE * 2 + 1)
            if len(data) > MAX_MESSAGE * 2:
                raise ProtocolError('oversized response')
            return json.loads(data)

    def exchange(self, kind, payload):
        with self.lock:
            epoch = self.current_epoch()
            return self.exchange_at(kind, payload, epoch)['result']

    def exchange_at(self, kind, payload, epoch):
        """会话切换组件专用；普通业务始终通过 current_epoch 发送。"""
        with self.lock:
            # 遥测在共享认证锁及认证等待结束后采样，不能发送排队前的位置。
            if callable(payload):
                payload = payload()
            request = self.codec.seal(kind, payload, epoch=epoch)
            response = self.transport(request)
            header, body = self.codec.open(response)
            if header['reply_to'] != request['header']['message_id'] or header['kind'] != 'business.reply' or header['epoch'] != epoch:
                raise ProtocolError('response correlation failed')
            if not isinstance(body, dict) or type(body.get('ok')) is not bool:
                raise ProtocolError('invalid business response')
            if not body['ok']:
                raise BusinessRejected('cloud rejected business request')
            if 'result' not in body:
                raise ProtocolError('missing business result')
            return body


class SessionTelemetryClient(EdgeClient):
    """独立传输锁，仅复用已认证会话；不认证、不切换、不执行任务。"""

    def exchange_at(self, kind, payload, epoch):
        if kind != 'telemetry':
            raise ProtocolError('telemetry channel cannot execute control operations')
        return super().exchange_at(kind, payload, epoch)
