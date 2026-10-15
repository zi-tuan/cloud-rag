#!/bin/bash
set -eu
k3s ctr images list | grep 'edge-runtime'
k3s kubectl get events -n uav-mvp --field-selector involvedObject.name=runtime-arm64-verification-336a51c6 --sort-by=.lastTimestamp
k3s kubectl get job runtime-arm64-verification-336a51c6 -n uav-mvp -o jsonpath='{.status}'
