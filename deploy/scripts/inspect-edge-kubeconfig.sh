#!/bin/bash
set -eu
k3s kubectl --kubeconfig /home/uav/uav-agent-mvp/mcp/kubeconfig get deployments -n uav-mvp
k3s kubectl --kubeconfig /home/uav/uav-agent-mvp/mcp/kubeconfig auth can-i create deployments.apps -n uav-mvp
k3s kubectl --kubeconfig /home/uav/uav-agent-mvp/mcp/kubeconfig auth can-i update deployments/scale -n uav-mvp
