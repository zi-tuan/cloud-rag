"""复用经过主机密钥核验的 SSH 与命名空间操作。"""
import json
from pathlib import Path
import paramiko

BASE = '/home/uav/uav-agent-mvp'


def connect(host, port=22):
    client = paramiko.SSHClient()
    client.load_host_keys(str(Path.home() / '.ssh/known_hosts'))
    client.connect(host, port=port, username='uav', key_filename=str(Path.home() / '.ssh/id_ed25519'), timeout=15)
    return client


def run(client, command, data=None, timeout=60):
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if data is not None:
        stdin.write(data)
        stdin.channel.shutdown_write()
    output, error = stdout.read().decode(), stderr.read().decode()
    if stdout.channel.recv_exit_status():
        # 不包含命令、stdin 或配置载荷；调用方不得在错误正文输出秘密。
        raise RuntimeError('remote deployment failed: ' + output + error)
    return output


def kubectl(client, arguments, body=None, timeout=60):
    return run(client, 'env KUBECONFIG=/home/uav/.kube/config kubectl -n uav-mvp ' + arguments,
               None if body is None else json.dumps(body), timeout)

