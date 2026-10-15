#!/bin/bash
set -eu
timeout 8 curl --noproxy '*' --ipv4 --connect-timeout 3 --max-time 8 \
  --cacert /home/uav/uav-agent-mvp/tunnel/ca.crt -sS -o /dev/null \
  -w 'Verified cloud API HTTP %{http_code}\n' https://localhost:18443/api/edge/exchange
