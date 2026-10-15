#!/bin/bash
set -u
k3s kubectl get pods -n uav-mvp -o wide
k3s kubectl get events -n uav-mvp --sort-by=.lastTimestamp | tail -12
grep -n -E 'SystemdCgroup|runtime_type|cgroup' /var/lib/rancher/k3s/agent/etc/containerd/config.toml
systemctl --version | head -1
