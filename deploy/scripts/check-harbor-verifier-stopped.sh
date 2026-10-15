#!/bin/bash
set -eu
python3 - <<'PY'
from pathlib import Path
needle = b'/home/uav/uav-agent-mvp/docs/archive/deploy/verify_harbor_jetson_oci.py'
found = []
for entry in Path('/proc').iterdir():
    if entry.name.isdigit():
        try:
            command = (entry / 'cmdline').read_bytes()
        except (OSError, IOError):
            continue
        if needle in command:
            found.append(entry.name)
print('harbor_verifier_running=' + str(bool(found)).lower())
PY
