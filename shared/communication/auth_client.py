"""认证容器客户端及身份准入后的密码服务组合；模型不接触证明。"""
from __future__ import annotations

import json
import urllib.error
from pydantic import ValidationError
from shared.communication.auth_models import ProofRequest, VerifyRequest
import urllib.parse
import urllib.request
from pathlib import Path

from shared.communication.auth_protocol import POLICIES

from shared.communication.envelope import MAX_MESSAGE, ProtocolError, canonical


class AuthServiceClient:
    def __init__(self, url, token, *, timeout=10):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or len(token) < 32:
            raise ProtocolError('invalid internal authentication endpoint')
        self.url, self.token, self.timeout = url.rstrip('/'), token, timeout

    def request(self, path, body=None):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        request = urllib.request.Request(self.url + path, None if body is None else canonical(body),
                                         {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=self.timeout) as response:
                data = response.read(MAX_MESSAGE * 2 + 1)
                if len(data) > MAX_MESSAGE * 2:
                    raise ProtocolError('oversized authentication response')
                return json.loads(data)
        except urllib.error.HTTPError as error:
            if error.code < 500:
                raise ProtocolError('authentication service rejected request') from error
            raise

    def ready(self):
        return self.request('/ready').get('ready') is True

    def prove(self, purpose, transcript, envelope=None):
        try:
            body = ProofRequest(purpose=purpose, transcript=transcript, envelope=envelope)
        except ValidationError as error:
            raise ProtocolError('invalid proof request') from error
        return self.request('/v1/prove', body.model_dump())['proof']

    def verify(self, purpose, transcript, proof, envelope=None):
        try:
            body = VerifyRequest(purpose=purpose, transcript=transcript, proof=proof, envelope=envelope)
        except ValidationError as error:
            raise ProtocolError('invalid proof request') from error
        result = self.request('/v1/verify', body.model_dump())
        if result != {'verified': True}:
            raise ProtocolError('identity proof rejected')


class PolicyProviders:
    """按持久化会话绑定路由到已登记服务；绝不自动尝试其他算法。"""
    def __init__(self, providers):
        if not providers or set(providers) - POLICIES:
            raise ProtocolError('invalid registered authentication policies')
        self.providers = dict(providers)

    def ready(self, policy):
        if policy not in self.providers:
            return False
        provider = self.providers[policy]
        if isinstance(provider, AuthServiceClient):
            result = provider.request('/ready')
            return result == {'policy': policy, 'ready': True}
        return provider.policy == policy and provider.ready()

    def provider(self, transcript):
        if transcript.get('policy') not in self.providers:
            raise ProtocolError('authentication policy is not registered')
        return self.providers[transcript['policy']]

    def prove(self, purpose, transcript, envelope=None):
        return self.provider(transcript).prove(purpose, transcript, envelope)

    def verify(self, purpose, transcript, proof, envelope=None):
        return self.provider(transcript).verify(purpose, transcript, proof, envelope)

    @classmethod
    def from_config(cls, config):
        entries = config.get('services') or {'Ed25519': config}
        return cls({policy: AuthServiceClient(entry['service_url'], Path(entry['token_file']).read_text().strip())
                    for policy, entry in entries.items()})


class AuthenticatedCodec:
    def __init__(self, codec, provider, sessions):
        self.inner, self.provider, self.sessions = codec, provider, sessions
        self.device_id, self.key_id, self.role = codec.device_id, codec.key_id, codec.role

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def seal(self, kind, payload, *, epoch, reply_to=None, auth_session_id=None):
        transcript = self.sessions.for_epoch(epoch) if auth_session_id is None else self.sessions.get(auth_session_id)
        envelope = self.inner.seal(kind, payload, epoch=epoch, reply_to=reply_to,
                                   auth_session_id=transcript['auth_session_id'])
        proof = self.provider.prove(self.role + '-message', transcript, envelope)
        return {**envelope, 'identity_proof': proof}

    def open(self, message):
        if not isinstance(message, dict) or set(message) != {'header', 'ciphertext', 'tag', 'identity_proof'}:
            raise ProtocolError('independent identity proof required')
        envelope = {k: message[k] for k in ('header', 'ciphertext', 'tag')}
        if not isinstance(envelope['header'], dict) or not isinstance(envelope['header'].get('auth_session_id'), str):
            raise ProtocolError('identity session binding required')
        transcript = self.sessions.get(envelope['header']['auth_session_id'])
        peer_role = 'cloud' if self.role == 'edge' else 'edge'
        self.provider.verify(peer_role + '-message', transcript, message['identity_proof'], envelope)
        header, payload = self.inner.open(envelope)
        self.sessions.admit(header)
        return header, payload
