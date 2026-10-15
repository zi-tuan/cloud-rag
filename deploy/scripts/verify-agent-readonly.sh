#!/bin/bash
set -eu
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- env PATH=/opt/uav/bin:/usr/local/bin:/usr/bin:/bin /opt/uav/bin/aivo pi --model mdoelscope1::Qwen/Qwen3.5-35B-A3B -- --print --no-session --thinking off '使用 MCP Kubernetes 工具查询 uav-mvp 命名空间的实际 Pod。报告 Pod 名称、Ready 状态和容器镜像。只读，不修改任何资源；不可猜测。如工具不可用则明确报告。请用中文简短回答。'
