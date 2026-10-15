"""准备专用转发密钥、主机固定与只读 registry 凭据；秘密不输出。"""
import io
import pathlib
import re

import paramiko


def connect(host, port):
    client = paramiko.SSHClient()
    client.load_host_keys(str(pathlib.Path.home() / '.ssh/known_hosts'))
    client.connect(host, port=port, username='uav', key_filename=str(pathlib.Path.home() / '.ssh/id_ed25519'),
                   timeout=10, banner_timeout=15, auth_timeout=15)
    client.get_transport().set_keepalive(10)
    return client


def run(client, command):
    _, out, err = client.exec_command(command, timeout=30)
    data = out.read()
    if out.channel.recv_exit_status() != 0:
        raise RuntimeError('Remote preparation failed')
    return data


def main():
    cloud = connect('47.109.49.98', 24531)
    edge = connect('192.168.8.101', 22)
    root = '/home/uav/uav-agent-mvp'
    try:
        run(edge, 'mkdir -p ' + root + '/tunnel && chmod 700 ' + root + '/tunnel')
        run(edge, "test -f " + root + "/tunnel/id_ed25519 || ssh-keygen -q -t ed25519 -N '' -C uav-phase1-forward-only -f " + root + '/tunnel/id_ed25519')
        with edge.open_sftp() as es, cloud.open_sftp() as cs:
            es.get_channel().settimeout(30)
            cs.get_channel().settimeout(30)
            public = es.open(root + '/tunnel/id_ed25519.pub').read().decode().strip()
            if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+ uav-phase1-forward-only', public):
                raise RuntimeError('Unexpected forwarding public key')
            key_path = '/home/uav/.ssh/authorized_keys'
            with cs.open(key_path) as source:
                original = source.read().decode()
            restrictions = 'restrict,port-forwarding,command="/bin/false",permitopen="127.0.0.1:35443",permitopen="127.0.0.1:38443" '
            line = restrictions + public
            if line not in original.splitlines():
                if public.split()[1] in original:
                    raise RuntimeError('Forwarding key already exists with different restrictions')
                with cs.open(key_path, 'a') as output:
                    output.write(('' if original.endswith('\n') else '\n') + line + '\n')
                cs.chmod(key_path, 0o600)
            host_key = cloud.get_transport().get_remote_server_key()
            files = {
                'known_hosts': ('[47.109.49.98]:24531 ' + host_key.get_name() + ' ' + host_key.get_base64() + '\n').encode(),
                'harbor-pull.json': cs.open(root + '/credentials/harbor-pull.json').read(),
            }
            for name, data in files.items():
                es.putfo(io.BytesIO(data), root + '/tunnel/' + name)
                es.chmod(root + '/tunnel/' + name, 0o600)
            unit = '''[Unit]
Description=UAV phase1 authenticated cloud forwarding
After=network-online.target
Wants=network-online.target

[Service]
User=uav
ExecStart=/usr/bin/ssh -N -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/home/uav/uav-agent-mvp/tunnel/known_hosts -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -i /home/uav/uav-agent-mvp/tunnel/id_ed25519 -p 24531 -L 127.0.0.1:35443:127.0.0.1:35443 -L 127.0.0.1:38443:127.0.0.1:38443 uav@47.109.49.98
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
UMask=0077

[Install]
WantedBy=multi-user.target
'''
            es.putfo(io.BytesIO(unit.encode()), root + '/deploy/uav-cloud-tunnel.service')
        print('Dedicated forwarding key authorized; CA and pull-only credential transferred; service not yet installed')
    finally:
        cloud.close()
        edge.close()


if __name__ == '__main__':
    main()
