#!/bin/bash
set -eu
root=/home/uav/uav-agent-mvp
for path in "$root/deploy/crypto-arm64.tar" "$root/deploy/images/crypto-image-lock.json" "$root/tls/ca.crt" "$root/credentials/harbor-pull.json"; do
  if [ -f "$path" ]; then stat -c 'present %s bytes %a %n' "$path"; else echo "missing $path"; fi
done
docker inspect --format 'harbor-core={{.State.Status}}' harbor-core
curl --noproxy '*' --ipv4 --silent --show-error --cacert "$root/tls/ca.crt" -o /dev/null -w 'harbor_v2_http=%{http_code}\n' https://localhost:15443/v2/ || true
