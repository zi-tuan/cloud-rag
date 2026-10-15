#!/bin/bash
set -eu
python3 - <<'PY'
import socket
try:
    with socket.create_connection(('192.168.8.110', 5763), timeout=4) as stream:
        stream.settimeout(4)
        data = stream.recv(4096)
        print('simulation_data_endpoint=connected received_bytes=%d application_bytes_sent=0' % len(data))
except OSError as exc:
    print('simulation_data_endpoint=unavailable error=%s errno=%s detail=%s application_bytes_sent=0' % (type(exc).__name__, exc.errno, exc.strerror))
import importlib.util
print('pymavlink_installed=%s' % (importlib.util.find_spec('pymavlink') is not None))
PY
