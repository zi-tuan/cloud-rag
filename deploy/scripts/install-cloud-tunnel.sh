#!/bin/bash
set -eu
source_unit=/home/uav/uav-agent-mvp/deploy/uav-cloud-tunnel.service
target_unit=/etc/systemd/system/uav-cloud-tunnel.service
if [ -f "$target_unit" ] && ! cmp -s "$source_unit" "$target_unit"; then
  echo 'Existing tunnel configuration differs; refusing overwrite' >&2
  exit 1
fi
install -m 644 "$source_unit" "$target_unit"
systemctl daemon-reload
systemctl enable --now uav-cloud-tunnel.service
systemctl is-active uav-cloud-tunnel.service
curl --noproxy '*' --ipv4 --silent --show-error --cacert /home/uav/uav-agent-mvp/tunnel/ca.crt -o /dev/null -w 'harbor_tls_http_status=%{http_code}\n' https://localhost:15443/v2/
