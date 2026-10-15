"""加密服务启动入口：加载配置并组装业务服务和 HTTP Controller。"""
import json
import logging
import os
from pathlib import Path

import uvicorn

from services.crypto_service.controller.crypto import create_app
from services.crypto_service.service.crypto import CryptoService

LOG = logging.getLogger('crypto-service')


def main():
    config = json.loads(Path(os.environ.get('UAV_CRYPTO_CONFIG', '/config/crypto.json')).read_text())
    config['version'] = os.environ.get('SERVICE_VERSION', config.get('version', 'v1'))
    config['broken'] = os.environ.get('BROKEN_READINESS', 'false') == 'true'
    token = Path(config['token_file']).read_text().strip()
    if len(token) < 32:
        raise ValueError('service token is too short')
    service = CryptoService(config)
    api = create_app(service, token)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    LOG.info('service starting suite=%s version=%s', service.suite, service.version)
    uvicorn.run(api, host=config.get('bind', '0.0.0.0'), port=int(config.get('port', 8080)), access_log=False)


if __name__ == '__main__':
    main()
