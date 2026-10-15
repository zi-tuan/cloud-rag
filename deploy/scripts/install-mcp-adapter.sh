#!/bin/bash
set -eu
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- env npm_config_registry=https://registry.npmmirror.com npm_config_ignore_scripts=true node /workspace/pi/packages/coding-agent/dist/cli.js install npm:pi-mcp-adapter@2.36.0
