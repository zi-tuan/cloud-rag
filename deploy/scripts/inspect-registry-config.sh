#!/bin/bash
set -eu
python3 - <<'PY'
import pathlib,re
for path in [pathlib.Path('/var/lib/rancher/k3s/agent/etc/containerd/config.toml'),*pathlib.Path('/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d').glob('*.toml'),*pathlib.Path('/var/lib/rancher/k3s/agent/etc/containerd/certs.d').glob('*/hosts.toml')]:
    if path.name=='hosts.toml' and not any(x in str(path) for x in ('15443','47.109')):
        continue
    text=path.read_text()
    text=re.sub(r'(?im)^(.*(?:password|username|auth|token).*?=).*$',r'\1 "<REDACTED>"',text)
    print(str(path)+'\n'+text)
PY
