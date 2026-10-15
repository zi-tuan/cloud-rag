#!/bin/bash
set -eu
kubectl get nodes -o wide
kubectl get deployments,statefulsets,pods,services -n uav -o wide
kubectl get deployments,statefulsets,pods,services -n uav-mvp -o wide 2>/dev/null || true
kubectl get configmaps,secrets -n uav --no-headers 2>/dev/null | awk '{print $1, $2}'
