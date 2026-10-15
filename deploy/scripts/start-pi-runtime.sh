#!/bin/bash
set -eu
k3s ctr images import /home/uav/uav-agent-mvp/deploy/node-arm64.tar >/dev/null
k3s ctr images tag docker.io/library/node:uav-pinned docker.io/library/node@sha256:f71fb9ca71b1b47d4d1a009af78147ed6cdc74f9c7cfc36cbde78ff985169051 >/dev/null
k3s kubectl apply -f /home/uav/uav-agent-mvp/deploy/jetson/pi-runtime.yaml
k3s kubectl rollout status deployment/pi-runtime -n uav-mvp --timeout=60s
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- node --version
