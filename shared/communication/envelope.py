"""版本化 AEAD 消息封装。密钥由配置注入，不传入模型或消息载荷。"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import uuid
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SUITES = {"AES-256-GCM": (algorithms.AES, 32), "SM4-GCM": (algorithms.SM4, 16)}
MAX_MESSAGE = 1024 * 1024
MAX_SEQ = (1 << 63) - 1
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
HEADER_FIELDS = {"v", "device_id", "key_id", "epoch", "suite", "direction", "seq", "message_id", "kind", "reply_to"}


class ProtocolError(ValueError):
    """无效消息，不应记录其明文或密钥。"""


class ReplayError(ProtocolError):
    """已接收的序列号或超过接收窗口的旧消息。"""


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def aead_encrypt(suite: str, key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> tuple[bytes, bytes]:
    algorithm, size = SUITES[suite]
    if len(key) != size or len(nonce) != 12:
        raise ProtocolError("invalid AEAD parameters")
    cipher = Cipher(algorithm(key), modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(aad)
    ciphertext = cipher.update(plaintext) + cipher.finalize()
    return ciphertext, cipher.tag


def aead_decrypt(suite: str, key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes, aad: bytes) -> bytes:
    algorithm, size = SUITES[suite]
    if len(key) != size or len(nonce) != 12 or len(tag) != 16:
        raise ProtocolError("invalid AEAD parameters")
    cipher = Cipher(algorithm(key), modes.GCM(nonce, tag)).decryptor()
    cipher.authenticate_additional_data(aad)
    # 不在 finalize 验证完成前返回任何明文。
    try:
        return cipher.update(ciphertext) + cipher.finalize()
    except InvalidTag as exc:
        raise ProtocolError("message authentication failed") from exc


class CounterStore(Protocol):
    """编解码器依赖计数器接口，不负责数据库连接或 ORM 映射。"""

    def allocate(self, domain: str) -> int: ...

    def accept(self, domain: str, seq: int) -> None: ...

    def schedule_retirement(self, epoch: str, suite: str, retire_at: float) -> None: ...

    def valid_retirement(self, epoch: str, now: float) -> None: ...


class EnvelopeCodec:
    def __init__(self, *, device_id: str, key_id: str, root_key: bytes, role: str, counters: CounterStore, epochs: dict[str, str], clock=time.time):
        if role not in ("edge", "cloud") or len(root_key) != 32:
            raise ProtocolError("invalid identity configuration")
        if not all(IDENTIFIER.fullmatch(x) for x in (device_id, key_id)):
            raise ProtocolError("invalid identity identifier")
        self.device_id, self.key_id = device_id, key_id
        self._root_key = root_key
        self.role, self.counters = role, counters
        self.clock = clock
        self._lock = threading.RLock()
        self._epochs: dict[str, str] = {}
        for epoch, suite in epochs.items():
            self.allow_epoch(epoch, suite)

    def allow_epoch(self, epoch: str, suite: str):
        if not isinstance(epoch, str) or not IDENTIFIER.fullmatch(epoch) or suite not in SUITES:
            raise ProtocolError("invalid session")
        with self._lock:
            if epoch in self._epochs and self._epochs[epoch] != suite:
                raise ProtocolError("epoch algorithm cannot change")
            self._epochs[epoch] = suite

    def retire_epoch(self, epoch: str):
        with self._lock:
            self._epochs.pop(epoch, None)

    def block_epoch(self, epoch):
        self.retire_epoch(epoch)

    def schedule_retirement(self, epoch, suite, retire_at):
        if not isinstance(epoch, str) or not IDENTIFIER.fullmatch(epoch) or suite not in SUITES:
            raise ProtocolError('invalid retirement binding')
        self.counters.schedule_retirement(epoch, suite, retire_at)

    def _session(self, epoch: str, direction: str) -> tuple[str, bytes, str]:
        with self._lock:
            suite = self._epochs.get(epoch)
        if suite is None:
            raise ProtocolError("unrecognized or retired epoch")
        self.counters.valid_retirement(epoch, self.clock())
        domain = canonical([self.device_id, self.key_id, epoch, suite, direction]).decode()
        digest = hashes.SM3() if suite == "SM4-GCM" else hashes.SHA256()
        key = HKDF(algorithm=digest, length=SUITES[suite][1], salt=b"uav-edge-envelope-v1", info=domain.encode()).derive(self._root_key)
        return suite, key, domain

    def seal(self, kind: str, payload: object, *, epoch: str, reply_to: str | None = None, auth_session_id: str | None = None) -> dict:
        if not isinstance(kind, str) or not IDENTIFIER.fullmatch(kind):
            raise ProtocolError("invalid message kind")
        if reply_to is not None and (not isinstance(reply_to, str) or not IDENTIFIER.fullmatch(reply_to)):
            raise ProtocolError("invalid reply identifier")
        plaintext = canonical(payload)
        if len(plaintext) > MAX_MESSAGE:
            raise ProtocolError("message too large")
        direction = "edge-to-cloud" if self.role == "edge" else "cloud-to-edge"
        suite, key, domain = self._session(epoch, direction)
        seq = self.counters.allocate(domain)
        header = dict(v=1, device_id=self.device_id, key_id=self.key_id, epoch=epoch, suite=suite, direction=direction, seq=seq, message_id=uuid.uuid4().hex, kind=kind, reply_to=reply_to)
        if auth_session_id is not None:
            if not isinstance(auth_session_id, str) or not IDENTIFIER.fullmatch(auth_session_id):
                raise ProtocolError('invalid authentication session')
            header.update(v=2, auth_session_id=auth_session_id)
        ciphertext, tag = aead_encrypt(suite, key, b"\0" * 4 + seq.to_bytes(8, "big"), plaintext, canonical(header))
        return {"header": header, "ciphertext": base64.b64encode(ciphertext).decode(), "tag": base64.b64encode(tag).decode()}

    def open(self, envelope: dict) -> tuple[dict, object]:
        try:
            if not isinstance(envelope, dict) or set(envelope) != {"header", "ciphertext", "tag"}:
                raise ProtocolError("invalid envelope fields")
            header = envelope["header"]
            if not isinstance(header, dict) or set(header) != (HEADER_FIELDS | {'auth_session_id'} if header.get('v') == 2 else HEADER_FIELDS):
                raise ProtocolError("invalid header fields")
            if type(header["v"]) is not int or header["v"] not in (1, 2) or type(header["seq"]) is not int or not 1 <= header["seq"] <= MAX_SEQ:
                raise ProtocolError("invalid version or sequence")
            if header['v'] == 2 and (not isinstance(header['auth_session_id'], str) or not IDENTIFIER.fullmatch(header['auth_session_id'])):
                raise ProtocolError('invalid authentication session')
            for name in ("device_id", "key_id", "epoch", "suite", "direction", "message_id", "kind"):
                if not isinstance(header[name], str) or not IDENTIFIER.fullmatch(header[name]):
                    raise ProtocolError("invalid header value")
            if header["reply_to"] is not None and (not isinstance(header["reply_to"], str) or not IDENTIFIER.fullmatch(header["reply_to"])):
                raise ProtocolError("invalid reply identifier")
            expected = "cloud-to-edge" if self.role == "edge" else "edge-to-cloud"
            if header["device_id"] != self.device_id or header["key_id"] != self.key_id or header["direction"] != expected:
                raise ProtocolError("wrong recipient or direction")
            suite, key, domain = self._session(header["epoch"], expected)
            if header["suite"] != suite:
                raise ProtocolError("suite does not match epoch")
            if not isinstance(envelope["ciphertext"], str) or len(envelope["ciphertext"]) > 4 * ((MAX_MESSAGE + 2) // 3):
                raise ProtocolError("invalid ciphertext size")
            if not isinstance(envelope["tag"], str) or len(envelope["tag"]) != 24:
                raise ProtocolError("invalid tag size")
            ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
            tag = base64.b64decode(envelope["tag"], validate=True)
            plaintext = aead_decrypt(suite, key, b"\0" * 4 + header["seq"].to_bytes(8, "big"), ciphertext, tag, canonical(header))
            payload = json.loads(plaintext, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")))
            self.counters.accept(domain, header["seq"])
            return dict(header), payload
        except ProtocolError:
            raise
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise ProtocolError("invalid message encoding") from exc
