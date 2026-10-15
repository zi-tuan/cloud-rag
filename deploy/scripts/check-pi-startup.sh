#!/bin/bash
set -eu
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- /opt/uav/bin/pi --version
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- cat /workspace/project/.mcp.json
k3s kubectl get job runtime-arm64-verification-336a51c6 -n uav-mvp -o jsonpath='{.status.conditions}'
