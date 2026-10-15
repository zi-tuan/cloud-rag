#!/bin/bash
set -eu
systemctl is-active uav-cloud-tunnel
journalctl -u uav-cloud-tunnel -n 8 --no-pager
curl --noproxy '*' --ipv4 --cacert /home/uav/uav-agent-mvp/tunnel/ca.crt -sS -o /dev/null -w 'Harbor HTTP %{http_code}\n' https://localhost:15443/v2/
