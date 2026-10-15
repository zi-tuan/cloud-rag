"""预登记证书链、用途、身份和 CRL 校验；不接受网络请求提供的信任材料。"""
from pathlib import Path
import subprocess
import threading

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from shared.communication.envelope import ProtocolError


def openssl(config, arguments, data=None):
    try:
        result = subprocess.run([config.get('openssl_binary', 'openssl'), *arguments], input=data,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise ProtocolError('authentication cryptographic runtime unavailable') from error
    if result.returncode:
        raise ProtocolError('certificate or cryptographic operation rejected')
    return result.stdout


class CertificateTrust:
    def __init__(self, config, clock):
        self.config, self.clock = config, clock
        version = openssl(config, ['version']).decode().split()[1]
        if not version.startswith('3.') or version != config['openssl_version']:
            raise ProtocolError('unverified OpenSSL version')
        self.leaves = {}
        self.leaf_bytes = {}
        self.lock = threading.RLock()
        self.verified_material = None
        self.verified_at = self.valid_until = 0
        for side in ('own', 'peer'):
            path = config[side + '_certificate_file']
            self.leaf_bytes[side] = Path(path).read_bytes()
            cert = x509.load_pem_x509_certificate(self.leaf_bytes[side])
            role = config['role'] if side == 'own' else ('cloud' if config['role'] == 'edge' else 'edge')
            identity = config['device_id'] if role == 'edge' else config['cloud_id']
            expected = 'urn:uav:' + role + ':' + identity
            try:
                uris = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.UniformResourceIdentifier)
                usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
                eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
                ca = cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
            except x509.ExtensionNotFound as error:
                raise ProtocolError('identity certificate extensions missing') from error
            if uris != [expected] or not usage.digital_signature or usage.key_cert_sign or ca:
                raise ProtocolError('certificate identity or usage rejected')
            purpose = ExtendedKeyUsageOID.CLIENT_AUTH if role == 'edge' else ExtendedKeyUsageOID.SERVER_AUTH
            if set(eku) != {purpose}:
                raise ProtocolError('certificate role rejected')
            self.leaves[side] = (cert, 'sslclient' if role == 'edge' else 'sslserver')
        self.check()

    def check(self):
        with self.lock:
            # 每次读取完整材料；CRL 更新不能等缓存 TTL，也不依赖文件时间戳。
            material = self.material()
            now = int(self.clock())
            for side in self.leaves:
                if material[(side, 'certificate')] != self.leaf_bytes[side]:
                    raise ProtocolError('identity certificate changed; restart credential provider')
            if material == self.verified_material and self.verified_at <= now < self.valid_until:
                return
            self.verified_material = None
            deadlines = []
            for side, (cert, purpose) in self.leaves.items():
                openssl(self.config, ['verify', '-x509_strict', '-check_ss_sig', '-crl_check', '-no-CApath', '-no-CAstore',
                                     '-attime', str(now), '-purpose', purpose,
                                     '-CAfile', self.config[side + '_ca_file'], '-CRLfile', self.config[side + '_crl_file'],
                                     self.config[side + '_certificate_file']])
                deadlines.append(cert.not_valid_after_utc.timestamp())
                try:
                    deadlines.extend(c.not_valid_after_utc.timestamp() for c in x509.load_pem_x509_certificates(material[(side, 'ca')]))
                    crl = x509.load_pem_x509_crl(material[(side, 'crl')])
                except ValueError as error:
                    raise ProtocolError('certificate trust material rejected') from error
                deadlines.append(crl.next_update_utc.timestamp() if crl.next_update_utc else now)
            if self.material() != material:
                raise ProtocolError('certificate trust material changed during verification')
            # 只复用已验证且内容完全相同的证书链；时间回退和任一到期边界重新校验。
            self.verified_material, self.verified_at, self.valid_until = material, now, min(deadlines)

    def material(self):
        return {(side, kind): Path(self.config[side + '_' + kind + '_file']).read_bytes()
                for side in self.leaves for kind in ('certificate', 'ca', 'crl')}
