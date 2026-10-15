#!/bin/bash
set -eu
# Jetson systemd 237 lacks AttachProcessesToUnit used by recent runc.
# Keep both kubelet and containerd on cgroupfs for this pinned K3s version.
template=/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.tmpl
test ! -e "$template"
sed 's/SystemdCgroup = true/SystemdCgroup = false/g' /var/lib/rancher/k3s/agent/etc/containerd/config.toml > "$template"
chmod 600 "$template"
sed -i '/  - fail-swap-on=false/a\  - cgroup-driver=cgroupfs' /etc/rancher/k3s/config.yaml
systemctl restart --no-block k3s
