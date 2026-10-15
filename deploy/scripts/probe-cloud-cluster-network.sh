#!/bin/bash
python3 - <<'PY'
import socket,importlib.util
print('paramiko_available',importlib.util.find_spec('paramiko') is not None)
for port in (22,6443):
    try:
        with socket.create_connection(('192.168.2.101',port),timeout=5) as s:
            print('master_port',port,'connected')
            if port==22:
                print(s.recv(256).decode(errors='replace'))
    except OSError as e:
        print('master_port',port,type(e).__name__)
PY
