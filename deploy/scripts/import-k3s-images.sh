#!/bin/bash
set -eu
install -d -m 700 /var/lib/rancher/k3s/agent/images
install -m 600 /home/uav/uav-agent-mvp/deploy/k3s-airgap-images-arm64.tar.gz /var/lib/rancher/k3s/agent/images/k3s-airgap-images-arm64.tar.gz
k3s ctr images import /var/lib/rancher/k3s/agent/images/k3s-airgap-images-arm64.tar.gz
