"""Run a local script or transfer a file over verified SSH; secrets stay in memory."""
import argparse
import os
import pathlib
import shlex
import sys
import paramiko

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

p = argparse.ArgumentParser()
p.add_argument('--host', default='192.168.8.101')
p.add_argument('--port', type=int, default=22)
p.add_argument('--sudo', action='store_true')
p.add_argument('--put', nargs=2, metavar=('LOCAL', 'REMOTE'))
p.add_argument('script', nargs='?')
a = p.parse_args()
c = paramiko.SSHClient()
c.load_host_keys(str(pathlib.Path.home() / '.ssh' / 'known_hosts'))
c.connect(a.host, port=a.port, username='uav', key_filename=str(pathlib.Path.home() / '.ssh' / 'id_ed25519'), timeout=15)
try:
    if a.put:
        with c.open_sftp() as s:
            s.put(*a.put)
        print('Transferred:', a.put[1])
    if a.script:
        command = 'env KUBECONFIG=/home/uav/.kube/config bash -se'
        if a.sudo:
            command = "sudo -S -p '' bash -se"
        stdin, stdout, stderr = c.exec_command(command)
        if a.sudo:
            stdin.write(os.environ['UAV_SUDO_PASSWORD'] + '\n')
            stdin.flush()
        stdin.write(pathlib.Path(a.script).read_text(encoding='utf-8'))
        stdin.flush()
        stdin.channel.shutdown_write()
        # Drain both streams while the remote command is active.
        import select
        channel = stdout.channel
        while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
            if channel.recv_ready():
                print(channel.recv(65536).decode(errors='replace'), end='', flush=True)
            if channel.recv_stderr_ready():
                print(channel.recv_stderr(65536).decode(errors='replace'), end='', file=sys.stderr, flush=True)
            select.select([channel], [], [], 0.1)
        sys.exit(channel.recv_exit_status())
finally:
    c.close()
