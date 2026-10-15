"""OpenSSL source evidence; stable UAV IDs are not deployment authority."""
from .legacy_catalog import ALIASES, service_id


def entry(label, terms, paths, binding):
    return dict(label=label, terms=terms, paths=paths, binding=binding,
                evidence='available', note='OpenSSL 源码及官方接口文档证据；未证明目标运行环境已启用算法或无人机服务已部署。')


SERVICES = {
    'sm4-gcm-v1': entry('SM4-GCM 通信加密', 'sm4 gcm encrypt decrypt',
        ['doc/man7/EVP_CIPHER-SM4.pod', 'providers/implementations/ciphers/cipher_sm4_gcm.c'],
        {'suite': 'SM4-GCM'}),
    'aes-gcm-v1': entry('AES-256-GCM 通信加密', 'aes gcm encrypt decrypt',
        ['doc/man7/EVP_CIPHER-AES.pod', 'providers/implementations/ciphers/cipher_aes_gcm.c'],
        {'suite': 'AES-256-GCM'}),
    'auth-sm2': entry('SM2 身份签名与验签', 'sm2 sign verify',
        ['doc/man7/EVP_SIGNATURE-SM2.pod', 'doc/man7/EVP_PKEY-SM2.pod',
         'providers/implementations/signature/sm2_sig.c'], {'auth_policy': 'SM2'}),
    'auth-rsa': entry('RSA-PSS 身份认证', 'rsa pss sign verify',
        ['doc/man7/EVP_SIGNATURE-RSA.pod', 'providers/implementations/signature/rsa_sig.c'],
        {'auth_policy': 'RSA'}),
    'auth-ed25519': entry('Ed25519 身份认证', 'ed25519 sign verify',
        ['doc/man7/EVP_SIGNATURE-ED25519.pod', 'providers/implementations/signature/eddsa_sig.c'],
        {'auth_policy': 'Ed25519'}),
    # Historical public IDs remain stable; the corpus identifies the library.
    'gmssl-sm3': entry('SM3 摘要与 HMAC', 'sm3 hmac digest',
        ['doc/man7/EVP_MD-SM3.pod', 'doc/man7/EVP_MAC-HMAC.pod', 'crypto/sm3/sm3.c'], {}),
    'gmssl-hkdf': entry('HKDF 密钥派生', 'hkdf extract expand',
        ['doc/man7/EVP_KDF-HKDF.pod', 'providers/implementations/kdfs/hkdf.c'], {}),
    'gmssl-x509': entry('X.509 证书和 CRL 校验', 'x509 certificate crl verify',
        ['doc/man7/x509.pod', 'crypto/x509/x509_vfy.c'], {}),
}
