#!/bin/bash
set -eu
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- env PATH=/opt/uav/bin:/usr/local/bin:/usr/bin:/bin /opt/uav/bin/aivo pi --model mdoelscope1::Qwen/Qwen3.5-35B-A3B -- --print --no-session --thinking off '你调用一下 MCP 看一下有哪些 k3s Pod、Deployment。请实际调用工具并报告名称和 Ready。'
