#!/bin/bash
set -eu
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- sh -c 'cd /workspace/pi && npm ci --ignore-scripts --registry=https://registry.npmmirror.com --fetch-retries=1 --fetch-timeout=45000 --no-audit --no-fund'
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- sh -c 'cd /workspace/pi && npm run build:offline'
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- node /workspace/pi/packages/coding-agent/dist/cli.js --version
