#!/bin/bash
set -eu
tar -xzf /home/uav/uav-agent-mvp/src/pi-model-data.tar.gz -C /home/uav/uav-agent-mvp/src/pi
chown -R uav:uav /home/uav/uav-agent-mvp/src/pi/packages/ai/src/providers/data
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- sh -c 'cd /workspace/pi && npm run build:offline'
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- node /workspace/pi/packages/coding-agent/dist/cli.js --version
