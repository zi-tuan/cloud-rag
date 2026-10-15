"""隔离的本地 Swagger 验证服务，不连接真实设备、集群或生产状态。"""
import argparse
from pathlib import Path
import secrets
import tempfile

import uvicorn

from cloud.cloud_api.api import CloudApplication, create_app as create_cloud_app
from cloud.cloud_api.tasks import TaskStore
from services.crypto_service.repository.crypto import CounterRepository
from shared.communication.envelope import EnvelopeCodec
from services.crypto_service.service.crypto import CryptoService
from services.crypto_service.controller.crypto import create_app as create_crypto_app

DEMO_TOKEN = 'swagger-local-demo-token-00000001'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service', choices=['cloud', 'crypto'], default='cloud')
    parser.add_argument('--port', type=int)
    args = parser.parse_args()
    port = args.port or (18080 if args.service == 'cloud' else 18081)
    with tempfile.TemporaryDirectory(prefix='uav-swagger-') as directory:
        root = Path(directory)
        if args.service == 'cloud':
            codec = EnvelopeCodec(device_id='demo-edge', key_id='demo-key', root_key=secrets.token_bytes(32),
                                  role='cloud', counters=CounterRepository(root / 'counter.db'), epochs={'demo': 'AES-256-GCM'})
            application = CloudApplication(TaskStore(root / 'tasks.db'), codec,
                                           login_verifier=lambda value: value == 'Bearer ' + DEMO_TOKEN)
            app = create_cloud_app(application)
        else:
            (root / 'key').write_bytes(secrets.token_bytes(32))
            (root / 'token').write_text(DEMO_TOKEN)
            application = CryptoService({
                'suite': 'AES-256-GCM', 'device_id': 'demo-edge', 'key_id': 'demo-key', 'role': 'edge',
                'key_file': str(root / 'key'), 'token_file': str(root / 'token'),
                'state_path': str(root / 'counter.db'), 'initialize': True, 'initial_epoch': 'demo',
            })
            app = create_crypto_app(application, DEMO_TOKEN)
        print(f'Swagger: http://127.0.0.1:{port}/docs', flush=True)
        print(f'仅限本地演示，Authorize 输入: {DEMO_TOKEN}', flush=True)
        uvicorn.run(app, host='127.0.0.1', port=port, access_log=False)


if __name__ == '__main__':
    main()
