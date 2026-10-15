"""Ed25519 身份协议 v1：固定上下文、角色分离和逐消息独立身份签名。"""
from __future__ import annotations

import base64
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from shared.communication.envelope import IDENTIFIER, SUITES, ProtocolError, canonical

FIELDS = {'v', 'device_id', 'cloud_id', 'policy', 'credential_version', 'policy_version',
          'key_id', 'epoch', 'suite', 'client_nonce', 'server_nonce', 'request_id',
          'auth_session_id', 'challenge_expires', 'session_expires', 'protection_profile', 'key_expires'}
PURPOSES = {'cloud-challenge': 'cloud', 'edge-response': 'edge',
            'edge-message': 'edge', 'cloud-message': 'cloud'}
POLICIES = frozenset({'Ed25519', 'RSA', 'SM2'})


def validate_transcript(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ProtocolError('invalid authentication transcript')
    if type(value['v']) is not int or value['v'] != 1 or value['policy'] not in POLICIES or value['policy_version'] != '1':
        raise ProtocolError('unsupported authentication policy')
    from shared.communication.protection import protection_policy
    protection_policy(value['protection_profile'])
    for key in FIELDS - {'v', 'challenge_expires', 'session_expires', 'key_expires'}:
        if not isinstance(value[key], str) or not IDENTIFIER.fullmatch(value[key]):
            raise ProtocolError('invalid authentication identifier')
    for key in ('client_nonce', 'server_nonce', 'auth_session_id'):
        if len(value[key]) != 64 or any(c not in '0123456789abcdef' for c in value[key]):
            raise ProtocolError('invalid authentication randomness')
    if value['suite'] not in SUITES:
        raise ProtocolError('unsupported communication suite')
    if any(type(value[k]) is not int for k in ('challenge_expires', 'session_expires', 'key_expires')) or not value['challenge_expires'] < value['session_expires'] <= value['key_expires']:
        raise ProtocolError('invalid authentication lifetime')
    return value


def proof_bytes(purpose, transcript, envelope=None):
    validate_transcript(transcript)
    if not isinstance(purpose, str) or purpose not in PURPOSES:
        raise ProtocolError('unsupported proof purpose')
    if purpose.endswith('-message'):
        if not isinstance(envelope, dict) or set(envelope) != {'header', 'ciphertext', 'tag'}:
            raise ProtocolError('invalid signed envelope')
        header = envelope['header']
        if not isinstance(header, dict):
            raise ProtocolError('invalid signed header')
        expected = {k: transcript[k] for k in ('device_id', 'key_id', 'epoch', 'suite', 'auth_session_id')}
        expected.update(v=2, direction='edge-to-cloud' if purpose == 'edge-message' else 'cloud-to-edge')
        if any(header.get(k) != v for k, v in expected.items()):
            raise ProtocolError('identity proof binding mismatch')
    elif envelope is not None:
        raise ProtocolError('unexpected proof payload')
    return canonical(['uav-identity-v1', purpose, transcript, envelope])


class Ed25519Provider:
    """仅供认证容器持有私钥；其他进程使用 AuthServiceClient。"""

    def __init__(self, config, *, clock=time.time):
        self.config, self.clock = config, clock
        if config['role'] not in ('edge', 'cloud'):
            raise ProtocolError('invalid authentication role')
        self.private = serialization.load_pem_private_key(Path(config['private_key_file']).read_bytes(), password=None)
        self.peer = serialization.load_pem_public_key(Path(config['peer_public_key_file']).read_bytes())
        if not isinstance(self.private, Ed25519PrivateKey) or not isinstance(self.peer, Ed25519PublicKey):
            raise ProtocolError('wrong authentication key type')

    policy = 'Ed25519'

    def ready(self):
        return not self.config.get('revoked', False) and self.config['not_before'] <= self.clock() < self.config['not_after']

    def check(self, transcript, purpose):
        validate_transcript(transcript)
        if transcript['policy'] != self.policy:
            raise ProtocolError('authentication algorithm mismatch')
        if not isinstance(purpose, str) or purpose not in PURPOSES:
            raise ProtocolError('unsupported proof purpose')
        for key in ('device_id', 'cloud_id', 'credential_version'):
            if transcript[key] != self.config[key]:
                raise ProtocolError('unregistered identity credential')
        if self.config.get('revoked', False) or not self.config['not_before'] <= self.clock() < self.config['not_after']:
            raise ProtocolError('credential expired or revoked')
        deadline = transcript['session_expires'] if purpose.endswith('-message') else transcript['challenge_expires']
        if self.clock() >= deadline:
            raise ProtocolError('authentication proof expired')

    def prove(self, purpose, transcript, envelope=None):
        self.check(transcript, purpose)
        if PURPOSES.get(purpose) != self.config['role']:
            raise ProtocolError('wrong signing role')
        return base64.b64encode(self.private.sign(proof_bytes(purpose, transcript, envelope))).decode('ascii')

    def verify(self, purpose, transcript, proof, envelope=None):
        self.check(transcript, purpose)
        if PURPOSES.get(purpose) in (None, self.config['role']):
            raise ProtocolError('wrong verification role')
        try:
            if not isinstance(proof, str) or len(proof) != 88:
                raise ValueError('invalid signature length')
            signature = base64.b64decode(proof, validate=True)
            self.peer.verify(signature, proof_bytes(purpose, transcript, envelope))
        except (InvalidSignature, ValueError, TypeError) as error:
            raise ProtocolError('identity proof rejected') from error
