"""Adapter IDs from the UAV registry; descriptions are not deployment authority."""
SERVICES = {
    'sm4-gcm-v1': dict(label='SM4-GCM 通信加密', terms='sm4 gcm encrypt decrypt',
        paths=['include/gmssl/sm4.h', 'src/sm4_gcm.c', 'tools/sm4_gcm.c', 'tests/sm4_gcmtest.c'],
        binding={'suite': 'SM4-GCM'}, evidence='available'),
    'aes-gcm-v1': dict(label='AES-256-GCM 通信加密', terms='aes gcm encrypt decrypt',
        paths=['include/gmssl/aes.h', 'src/aes.c', 'tests/aestest.c'],
        binding={'suite': 'AES-256-GCM'}, evidence='available'),
    'auth-sm2': dict(label='SM2 身份签名与验签', terms='sm2 sign verify',
        paths=['include/gmssl/sm2.h', 'src/sm2_sign.c', 'tools/sm2sign.c', 'tools/sm2verify.c'],
        binding={'auth_policy': 'SM2'}, evidence='available'),
    'auth-rsa': dict(label='RSA 身份认证', terms='rsa pss sign verify',
        paths=['include/gmssl/rsa.h', 'src/rsa.c'], binding={'auth_policy': 'RSA'},
        evidence='insufficient', note='本地 RSA 文件主要是公钥打印；不能据此证明 RSA-PSS 服务实现。'),
    'auth-ed25519': dict(label='Ed25519 身份认证', terms='ed25519', paths=[],
        binding={'auth_policy': 'Ed25519'}, evidence='not_found',
        note='当前 GmSSL 资料没有 Ed25519 实现证据；需补充无人机认证服务文档。'),
    'gmssl-sm3': dict(label='SM3 摘要与 HMAC', terms='sm3 hmac digest',
        paths=['include/gmssl/sm3.h', 'src/sm3_hmac.c', 'tools/sm3.c'], binding={}, evidence='available'),
    'gmssl-hkdf': dict(label='HKDF 密钥派生', terms='hkdf extract expand',
        paths=['include/gmssl/hkdf.h', 'src/hkdf.c'], binding={}, evidence='available'),
    'gmssl-x509': dict(label='X.509 证书和 CRL 校验', terms='x509 certificate crl verify',
        paths=['include/gmssl/x509.h', 'src/x509_vrf.c', 'tools/certverify.c'], binding={}, evidence='available'),
}

ALIASES = {'SM4-GCM': 'sm4-gcm-v1', 'AES-256-GCM': 'aes-gcm-v1',
           'SM2': 'auth-sm2', 'RSA': 'auth-rsa', 'Ed25519': 'auth-ed25519',
           'SM3': 'gmssl-sm3', 'HKDF': 'gmssl-hkdf', 'X509': 'gmssl-x509'}


def service_id(value):
    return ALIASES.get(value, value)
