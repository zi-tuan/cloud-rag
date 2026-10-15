#!/bin/bash
set -eu
k3s kubectl rollout restart deployment/pi-runtime -n uav-mvp
k3s kubectl rollout status deployment/pi-runtime -n uav-mvp --timeout=60s
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- node --version
k3s kubectl get pods -A
