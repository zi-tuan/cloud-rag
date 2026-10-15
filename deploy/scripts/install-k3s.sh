#!/bin/bash
set -eu
test ! -e /etc/systemd/system/k3s.service
test ! -e /etc/rancher/k3s/config.yaml
install -m 755 /home/uav/uav-agent-mvp/bin/k3s /usr/local/bin/k3s
install -d -m 700 /etc/rancher/k3s
cat > /etc/rancher/k3s/config.yaml <<'YAML'
node-name: jetson-uav-01
bind-address: 192.168.8.101
advertise-address: 192.168.8.101
node-ip: 192.168.8.101
write-kubeconfig-mode: "0600"
flannel-backend: host-gw
disable-network-policy: true
prefer-bundled-bin: true
disable:
  - traefik
  - servicelb
  - metrics-server
kubelet-arg:
  - fail-swap-on=false
kube-proxy-arg:
  - iptables-localhost-nodeports=false
  - conntrack-tcp-be-liberal=true
YAML
chmod 600 /etc/rancher/k3s/config.yaml
INSTALL_K3S_SKIP_DOWNLOAD=true INSTALL_K3S_SKIP_START=true INSTALL_K3S_EXEC=server sh /home/uav/uav-agent-mvp/deploy/k3s-install.sh
systemctl start --no-block k3s
systemctl is-enabled k3s
