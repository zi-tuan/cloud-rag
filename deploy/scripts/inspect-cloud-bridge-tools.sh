#!/bin/bash
set -u
id
command -v sshpass || true
python3 -c "import importlib.util; print('remote_paramiko=' + str(importlib.util.find_spec('paramiko') is not None))"
if sudo -n true 2>/dev/null; then echo sudo_noprompt=true; else echo sudo_noprompt=false; fi
systemctl --user is-active default.target 2>/dev/null || true
systemctl --user is-enabled-linger 2>/dev/null || true
