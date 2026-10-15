"""经已校验 SSH 跳板执行集群脚本；节点密码只由环境提供。"""
import argparse
import os
from pathlib import Path
import sys
import paramiko

sys.stdout.reconfigure(encoding='utf-8')
parser=argparse.ArgumentParser()
parser.add_argument('script')
parser.add_argument('--host',default='192.168.2.101')
parser.add_argument('--sudo',action='store_true')
args=parser.parse_args()
hosts=str(Path.home()/'.ssh/known_hosts')
jump=paramiko.SSHClient()
jump.load_host_keys(hosts)
jump.connect('47.109.49.98',port=24531,username='uav',key_filename=str(Path.home()/'.ssh/id_ed25519'),timeout=10,banner_timeout=15)
node=paramiko.SSHClient()
node.load_host_keys(hosts)
try:
    channel=jump.get_transport().open_channel('direct-tcpip',(args.host,22),('127.0.0.1',0))
    node.connect(args.host,username='ubuntu',password=os.environ['UAV_CLUSTER_PASSWORD'],sock=channel,timeout=10,banner_timeout=15)
    command="sudo -S -p '' bash -se" if args.sudo else 'bash -se'
    stdin,stdout,stderr=node.exec_command(command,timeout=45)
    if args.sudo:
        stdin.write(os.environ['UAV_CLUSTER_SUDO_PASSWORD']+'\n')
    stdin.write(Path(args.script).read_text(encoding='utf-8'))
    stdin.flush()
    stdin.channel.shutdown_write()
    print(stdout.read().decode(),end='')
    print(stderr.read().decode(),end='',file=sys.stderr)
    sys.exit(stdout.channel.recv_exit_status())
finally:
    node.close()
    jump.close()
