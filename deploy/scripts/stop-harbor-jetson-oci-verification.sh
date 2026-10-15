#!/bin/bash
set -eu
python3 - <<'PY'
import os
import signal
from pathlib import Path

needle = b'/home/uav/uav-agent-mvp/docs/archive/deploy/verify_harbor_jetson_oci.py'
stopped = []
for entry in Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    try:
        command = (entry / 'cmdline').read_bytes().replace(b'\0', b' ').strip()
    except (OSError, IOError):
        continue
    if needle in command and int(entry.name) != os.getpid():
        os.kill(int(entry.name), signal.SIGTERM)
        stopped.append(entry.name)
print('stopped_verifier_pids=' + ','.join(stopped))
PY
