"""固定 RSA-PSS/SHA-256 和 SM2/SM3 提供者，共用受约束身份协议。"""
import base64
from pathlib import Path
import tempfile
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils

from shared.communication.auth_certificates import CertificateTrust, openssl
from shared.communication.auth_protocol import Ed25519Provider, PURPOSES, proof_bytes
from shared.communication.envelope import ProtocolError


class RSAProvider(Ed25519Provider):
    policy = 'RSA'

    def __init__(self, config, *, clock=time.time):
        self.config, self.clock = config, clock
        if config['role'] not in ('edge', 'cloud'):
            raise ProtocolError('invalid authentication role')
        self.trust = CertificateTrust(config, clock)
        self.private = serialization.load_pem_private_key(Path(config['private_key_file']).read_bytes(), password=None)
        self.peer = self.trust.leaves['peer'][0].public_key()
        own = self.trust.leaves['own'][0].public_key()
        if any(not isinstance(key, kind) or key.key_size < 3072 for key, kind in
               ((self.private, rsa.RSAPrivateKey), (self.peer, rsa.RSAPublicKey), (own, rsa.RSAPublicKey))):
            raise ProtocolError('RSA requires registered keys of at least 3072 bits')
        if own.public_numbers() != self.private.public_key().public_numbers():
            raise ProtocolError('private key does not match identity certificate')

    def ready(self):
        if not super().ready():
            return False
        self.trust.check()
        return True

    def check(self, transcript, purpose):
        super().check(transcript, purpose)
        self.trust.check()

    @staticmethod
    def pss():
        return padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32)

    def prove(self, purpose, transcript, envelope=None):
        self.check(transcript, purpose)
        if PURPOSES[purpose] != self.config['role']:
            raise ProtocolError('wrong signing role')
        signature = self.private.sign(proof_bytes(purpose, transcript, envelope), self.pss(), hashes.SHA256())
        return base64.b64encode(signature).decode('ascii')

    def verify(self, purpose, transcript, proof, envelope=None):
        self.check(transcript, purpose)
        if PURPOSES[purpose] == self.config['role']:
            raise ProtocolError('wrong verification role')
        try:
            if not isinstance(proof, str) or len(proof) > 2048:
                raise ValueError('invalid RSA signature')
            signature = base64.b64decode(proof, validate=True)
            if len(signature) != (self.peer.key_size + 7) // 8:
                raise ValueError('invalid RSA signature size')
            self.peer.verify(signature, proof_bytes(purpose, transcript, envelope), self.pss(), hashes.SHA256())
        except (ValueError, TypeError, InvalidSignature) as error:
            raise ProtocolError('identity proof rejected') from error


class SM2Provider(RSAProvider):
    policy = 'SM2'

    def __init__(self, config, *, clock=time.time):
        self.config, self.clock = config, clock
        if config['role'] not in ('edge', 'cloud') or config.get('sm2_id') != '1234567812345678':
            raise ProtocolError('invalid SM2 role or distinguishing identifier')
        self.trust = CertificateTrust(config, clock)
        public = openssl(config, ['pkey', '-in', config['private_key_file'], '-pubout'])
        own = openssl(config, ['x509', '-in', config['own_certificate_file'], '-pubkey', '-noout'])
        self.peer_pem = openssl(config, ['x509', '-in', config['peer_certificate_file'], '-pubkey', '-noout'])
        if public != own:
            raise ProtocolError('private key does not match SM2 certificate')
        for key in (public, self.peer_pem):
            if b'ASN1 OID: SM2' not in openssl(config, ['pkey', '-pubin', '-text', '-noout'], key):
                raise ProtocolError('SM2 key required')

    def prove(self, purpose, transcript, envelope=None):
        self.check(transcript, purpose)
        if PURPOSES[purpose] != self.config['role']:
            raise ProtocolError('wrong signing role')
        signature = openssl(self.config, ['dgst', '-sm3', '-sign', self.config['private_key_file'],
                                         '-sigopt', 'distid:' + self.config['sm2_id']],
                            proof_bytes(purpose, transcript, envelope))
        return base64.b64encode(signature).decode('ascii')

    def verify(self, purpose, transcript, proof, envelope=None):
        self.check(transcript, purpose)
        if PURPOSES[purpose] == self.config['role']:
            raise ProtocolError('wrong verification role')
        try:
            if not isinstance(proof, str) or not 88 <= len(proof) <= 100:
                raise ValueError('invalid SM2 signature')
            signature = base64.b64decode(proof, validate=True)
            r, s = utils.decode_dss_signature(signature)
            order = int('FFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123', 16)
            if not 0 < r < order or not 0 < s < order or utils.encode_dss_signature(r, s) != signature:
                raise ValueError('noncanonical SM2 signature')
            with tempfile.TemporaryDirectory(prefix='uav-sm2-') as directory:
                public, proof_file = Path(directory) / 'peer.pem', Path(directory) / 'proof.der'
                public.write_bytes(self.peer_pem)
                proof_file.write_bytes(signature)
                openssl(self.config, ['dgst', '-sm3', '-verify', str(public), '-signature', str(proof_file),
                                      '-sigopt', 'distid:' + self.config['sm2_id']],
                        proof_bytes(purpose, transcript, envelope))
        except (ValueError, TypeError) as error:
            raise ProtocolError('identity proof rejected') from error


def provider_from_config(config, **kwargs):
    providers = {'Ed25519': Ed25519Provider, 'RSA': RSAProvider, 'SM2': SM2Provider}
    policy = config.get('policy', 'Ed25519')
    if policy not in providers:
        raise ProtocolError('unregistered authentication policy')
    return providers[policy](config, **kwargs)
