#!/bin/bash
set -eu
kubectl get storageclass
kubectl get pv,pvc -A
kubectl get svc -A
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.taints}{"\n"}{end}'
df -h /
id
command -v docker
docker info --format '{{.DockerRootDir}}' 2>/dev/null || true
