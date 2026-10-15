"""会话生命周期与加解密调度，不依赖 HTTP 请求或响应类型。"""
from pathlib import Path
import threading
import time
import math

from shared.communication.envelope import IDENTIFIER, EnvelopeCodec, ProtocolError, SUITES, aead_decrypt, aead_encrypt
from services.crypto_service.repository.crypto import CryptoRepository


class CryptoService:
    def __init__(self, config: dict, *, clock=time.time):
        self.suite = config['suite']
        if self.suite not in SUITES:
            raise ValueError('unsupported suite')
        self.version = config.get('version', 'v1')
        self.broken = config.get('broken', False)
        state_path = Path(config['state_path'])
        if not state_path.exists() and not config.get('initialize', False):
            raise ValueError('state missing: restore state or provision a new key and epoch')
        self.repository = CryptoRepository(state_path)
        self.clock = clock
        self._lock = threading.RLock()
        if config.get('initial_epoch'):
            self._validate_epoch(config['initial_epoch'])
            self._prepare_epoch(config['initial_epoch'])
        self.codec = EnvelopeCodec(
            device_id=config['device_id'], key_id=config['key_id'],
            root_key=Path(config['key_file']).read_bytes(), role=config['role'],
            counters=self.repository, epochs=self.repository.epochs(self.suite), clock=self.clock,
        )
        # 启动时验证当前架构和密码库确实支持指定算法。
        key, nonce = bytes(SUITES[self.suite][1]), bytes(12)
        ciphertext, tag = aead_encrypt(self.suite, key, nonce, b'capability-check', b'health')
        if aead_decrypt(self.suite, key, nonce, ciphertext, tag, b'health') != b'capability-check':
            raise RuntimeError('cipher self-test failed')

    @staticmethod
    def _validate_epoch(epoch: str):
        if not isinstance(epoch, str) or not IDENTIFIER.fullmatch(epoch):
            raise ProtocolError('invalid session')

    def _prepare_epoch(self, epoch: str):
        if self.repository.prepare_epoch(epoch, self.suite) != self.suite:
            raise ProtocolError('conflicting epoch')

    def prepare(self, epoch: str):
        self._validate_epoch(epoch)
        with self._lock:
            self.repository.valid_lease(epoch, self.clock())
            self._prepare_epoch(epoch)
            # 提交成功后才让编解码器接受会话。
            self.codec.allow_epoch(epoch, self.suite)

    def lease(self, epoch, protection_profile, expires_at):
        from shared.communication.protection import protection_policy
        self._validate_epoch(epoch)
        ttl = protection_policy(protection_profile)['key_ttl']
        if type(expires_at) is not int or not 0 < expires_at - self.clock() <= ttl:
            raise ProtocolError('invalid registered key lifetime')
        self.repository.lease(epoch, protection_profile, expires_at)

    def retire(self, epoch: str):
        self._validate_epoch(epoch)
        with self._lock:
            self.repository.retire_epoch(epoch, self.suite)
            self.codec.retire_epoch(epoch)

    def schedule_retirement(self, epoch, retire_at):
        self._validate_epoch(epoch)
        if type(retire_at) not in (int, float) or not math.isfinite(retire_at) or not 0 < retire_at <= self.clock() + 30:
            raise ProtocolError('invalid transition retirement deadline')
        with self._lock:
            self.repository.schedule_retirement(epoch, self.suite, retire_at)

    def seal(self, kind: str, payload: dict, *, epoch: str, reply_to: str | None = None,
             auth_session_id: str | None = None) -> dict:
        with self._lock:
            self.repository.valid_lease(epoch, self.clock())
            return self.codec.seal(kind, payload, epoch=epoch, reply_to=reply_to,
                                   auth_session_id=auth_session_id)

    def open(self, envelope: dict) -> tuple[dict, object]:
        with self._lock:
            self.repository.valid_lease(envelope['header']['epoch'], self.clock())
            return self.codec.open(envelope)
