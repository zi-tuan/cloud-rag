"""在目标主机受限目录生成长期 Ed25519 身份；不会覆盖现有凭据。"""
import argparse
import json
import os
from pathlib import Path
import secrets
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def provision(directory, role, device_id, cloud_id, version='1'):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    config = {'role': role, 'device_id': device_id, 'cloud_id': cloud_id, 'credential_version': version,
              'private_key_file': '/identity/private.pem', 'peer_public_key_file': '/identity/peer.pem',
              'token_file': '/identity/service.token', 'not_before': int(time.time()) - 60,
              'not_after': int(time.time()) + 365 * 86400, 'revoked': False}
    # 只有全新目录可以初始化；中断后保留已有文件，禁止隐式重置长期身份。
    if any(directory.iterdir()):
        raise ValueError('identity directory must be empty; restore existing credentials instead')
    key = Ed25519PrivateKey.generate()
    data = {
        'private.pem': key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        'public.pem': key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo),
        'service.token': secrets.token_hex(32).encode(),
        'auth.json': (json.dumps(config, indent=2) + '\n').encode(),
    }
    for name, content in data.items():
        descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as file:
            file.write(content)
    return directory / 'public.pem'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    parser.add_argument('--role', required=True, choices=['edge', 'cloud'])
    parser.add_argument('--device-id', required=True)
    parser.add_argument('--cloud-id', default='uav-cloud-01')
    args = parser.parse_args()
    print('Public registration material:', provision(args.directory, args.role, args.device_id, args.cloud_id))
