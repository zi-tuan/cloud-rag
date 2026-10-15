"""显式首次初始化，不覆盖数据库。部署者先创建受限目录及凭据文件。"""
import argparse
import json
import os
from pathlib import Path

from shared.communication.crypto_client import CryptoServiceCodec
from shared.communication.switch import SwitchState


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('config')
    parser.add_argument('--role', choices=['cloud', 'edge'], required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    codec = CryptoServiceCodec(device_id=config['device_id'], key_id=config['key_id'], role=args.role,
                               services=config['crypto_services'], token=Path(config['service_token_file']).read_text().strip())
    paths = [Path(config['switch_state'])]
    if args.role == 'cloud':
        paths.append(Path(config['task_state']))
    else:
        paths.append(Path(config['execution_state']))
    if any(path.exists() for path in paths):
        raise SystemExit('State exists: refusing reinitialization; restore or reconcile explicitly.')
    # O_EXCL 防止并发初始化；失败保留现场，不删库后盲目重试。
    for path in paths:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    SwitchState(paths[0], codec, config['initial_epoch'], config['initial_suite'], initialize=True)
    if args.role == 'cloud':
        from cloud.cloud_api.tasks import TaskStore
        TaskStore(paths[1])
    else:
        from jetson.edge_agent.executor import ExecutionJournal
        ExecutionJournal(paths[1], initialize=True)
    print('Control state initialized. Crypto nonce state must be independently provisioned and retained.')


if __name__ == '__main__':
    main()
