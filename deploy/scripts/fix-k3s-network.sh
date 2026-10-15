#!/bin/bash
set -eu
# Expose the API only on this private LAN address, never on a public interface.
sed -i 's/bind-address: 127.0.0.1/bind-address: 192.168.8.101/; s/advertise-address: 127.0.0.1/advertise-address: 192.168.8.101/' /etc/rancher/k3s/config.yaml
if modprobe xt_nfacct; then
  printf 'xt_nfacct\n' > /etc/modules-load.d/uav-k3s.conf
else
  if ! grep -q '^kube-proxy-arg:' /etc/rancher/k3s/config.yaml; then
    cat >> /etc/rancher/k3s/config.yaml <<'YAML'
kube-proxy-arg:
  - iptables-localhost-nodeports=false
  - conntrack-tcp-be-liberal=true
YAML
  fi
fi
systemctl restart --no-block k3s
# The scoped host-side bootstrap kubeconfig follows the private API endpoint.
sed -i 's#https://127.0.0.1:6443#https://192.168.8.101:6443#' /home/uav/uav-agent-mvp/mcp/kubeconfig
