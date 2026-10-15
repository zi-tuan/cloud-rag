#!/bin/bash
systemctl is-active k3s
/usr/local/bin/k3s kubectl get nodes -o wide --request-timeout=10s
/usr/local/bin/k3s kubectl get pods -A --request-timeout=10s
journalctl -u k3s --no-pager -n 35 | sed -E 's/token=[^ ]+/token=[REDACTED]/g'
