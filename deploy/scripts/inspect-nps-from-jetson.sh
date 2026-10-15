#!/bin/bash
set -eu
ssh -F /dev/null -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/home/uav/uav-agent-mvp/tunnel/known_hosts \
  -o ConnectTimeout=8 -i /home/uav/uav-agent-mvp/tunnel/id_ed25519 \
  -p 24531 uav@47.109.49.98 \
  "ss -lntp | grep -E '15443|8443' || true; docker ps --format '{{.Names}} {{.Status}}' | grep -E 'harbor|uav-cloud' || true; for p in 15443 8443; do timeout 4 curl --noproxy '*' -k -sS -o /dev/null -w 'port=\$p http=%{http_code}\\n' https://127.0.0.1:\$p/v2/ || true; done"
