#!/bin/bash
set -eu
# 新交互 shell 必须从用户环境获得默认 kubeconfig，而非依赖 SSH 调用者传入。
env -u KUBECONFIG bash -ic 'test "$KUBECONFIG" = /home/uav/.kube/config && k3s kubectl --request-timeout=10s get nodes'
k3s kubectl --kubeconfig /home/uav/.kube/config --request-timeout=10s auth can-i '*' '*'
k3s kubectl --kubeconfig /home/uav/uav-agent-mvp/mcp/kubeconfig --request-timeout=10s get deployments -n uav-mvp
