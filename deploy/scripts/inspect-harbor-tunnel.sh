#!/bin/bash
set -eu
ss -lnt | grep -E '127\.0\.0\.1:15443|127\.0\.0\.1:18443' || true
timeout 8 curl --noproxy '*' --ipv4 --connect-timeout 3 --max-time 8 --cacert /home/uav/uav-agent-mvp/tunnel/ca.crt -sS -o /dev/null -w 'Harbor HTTP %{http_code}\n' https://localhost:15443/v2/ || true
timeout 8 curl --noproxy '*' --ipv4 --connect-timeout 3 --max-time 8 -k -sS -o /dev/null -w 'Cloud bridge HTTP %{http_code}\n' https://localhost:18443/api/edge/exchange || true
