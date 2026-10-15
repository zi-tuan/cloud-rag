#!/bin/bash
set -eu
mkdir -p /home/uav/uav-agent-mvp/{bin,deploy,logs,src}
chmod 700 /home/uav/uav-agent-mvp
python3 - <<'PY'
import socket
for h,p in [('192.168.8.110',5762),('192.168.8.110',5763),('47.109.49.98',30881)]:
    try:
        s=socket.create_connection((h,p),3)
        s.close()
        print(h,p,'reachable')
    except OSError as e:
        print(h,p,type(e).__name__)
PY
