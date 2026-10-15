"""在原主机生成 RSA/SM2 身份和独立签发材料；仅公有信任材料可登记到对端。"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import time


def provision(directory, issuer_directory, role, policy, device_id, cloud_id, *, binary='openssl'):
    directory, issuer_directory = Path(directory), Path(issuer_directory)
    if role not in ('edge', 'cloud') or policy not in ('RSA', 'SM2'):
        raise ValueError('invalid certificate identity')
    for value in (device_id, cloud_id):
        import re
        if not re.fullmatch('[A-Za-z0-9_.:-]{1,128}', value):
            raise ValueError('invalid registered identity')
    for target in (directory, issuer_directory):
        target.mkdir(parents=True, mode=0o700, exist_ok=True)
        if any(target.iterdir()):
            raise ValueError('refusing to replace an existing identity or issuer')

    def execute(*args):
        result = subprocess.run([binary, *map(str, args)], capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError('identity provisioning failed')
        return result.stdout

    version = execute('version').decode().split()[1]
    if not version.startswith('3.'):
        raise ValueError('verified OpenSSL 3 runtime required')
    digest = 'sha256' if policy == 'RSA' else 'sm3'
    for key in (directory / 'private.pem', issuer_directory / 'ca.key'):
        if policy == 'RSA':
            execute('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072', '-out', key)
        else:
            execute('genpkey', '-algorithm', 'SM2', '-out', key)
        os.chmod(key, 0o600)
    execute('req', '-new', '-x509', '-' + digest, '-key', issuer_directory / 'ca.key', '-days', '3650',
            '-subj', '/CN=uav-' + role + '-' + policy + '-issuer', '-out', directory / 'own-ca.pem',
            '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
            '-addext', 'keyUsage=critical,keyCertSign,cRLSign', '-addext', 'subjectKeyIdentifier=hash')
    identity = device_id if role == 'edge' else cloud_id
    execute('req', '-new', '-' + digest, '-key', directory / 'private.pem', '-subj', '/CN=' + identity,
            '-out', issuer_directory / 'request.pem')
    (issuer_directory / 'index').write_text('')
    (issuer_directory / 'serial').write_text(secrets.token_hex(16) + '\n')
    (issuer_directory / 'crlnumber').write_text('01\n')
    (issuer_directory / 'issued').mkdir()
    config = issuer_directory / 'issuer.cnf'
    config.write_text(f'''[ca]
default_ca = issuer
[issuer]
database = {issuer_directory.as_posix()}/index
serial = {issuer_directory.as_posix()}/serial
crlnumber = {issuer_directory.as_posix()}/crlnumber
new_certs_dir = {issuer_directory.as_posix()}/issued
certificate = {directory.as_posix()}/own-ca.pem
private_key = {issuer_directory.as_posix()}/ca.key
default_md = {digest}
default_days = 365
default_crl_days = 365
policy = identity_policy
crl_extensions = crl_extensions
[identity_policy]
commonName = supplied
[leaf_extensions]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = {'clientAuth' if role == 'edge' else 'serverAuth'}
subjectAltName = URI:urn:uav:{role}:{identity}
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
[crl_extensions]
authorityKeyIdentifier = keyid:always
''')
    execute('ca', '-batch', '-notext', '-config', config, '-in', issuer_directory / 'request.pem',
            '-out', directory / 'public.pem', '-extensions', 'leaf_extensions')
    execute('ca', '-gencrl', '-config', config, '-out', directory / 'own-crl.pem')
    base = '/identity/'
    now = int(time.time())
    document = {'role': role, 'policy': policy, 'device_id': device_id, 'cloud_id': cloud_id,
                'credential_version': '1', 'private_key_file': base + 'private.pem',
                'own_certificate_file': base + 'public.pem', 'peer_certificate_file': base + 'peer.pem',
                'own_ca_file': base + 'own-ca.pem', 'peer_ca_file': base + 'peer-ca.pem',
                'own_crl_file': base + 'own-crl.pem', 'peer_crl_file': base + 'peer-crl.pem',
                'token_file': base + 'service.token', 'not_before': now - 60, 'not_after': now + 365 * 86400,
                'revoked': False, 'openssl_version': version, 'sm2_id': '1234567812345678'}
    (directory / 'auth.json').write_text(json.dumps(document, indent=2) + '\n')
    (directory / 'service.token').write_text(secrets.token_hex(32))
    for path in directory.iterdir():
        os.chmod(path, 0o600)
    return document


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    parser.add_argument('issuer_directory')
    parser.add_argument('--role', required=True, choices=['edge', 'cloud'])
    parser.add_argument('--policy', required=True, choices=['RSA', 'SM2'])
    parser.add_argument('--device-id', default='jetson-uav-01')
    parser.add_argument('--cloud-id', default='uav-cloud-01')
    args = parser.parse_args()
    provision(args.directory, args.issuer_directory, args.role, args.policy, args.device_id, args.cloud_id)
    print('Certificate identity generated on its original host')
