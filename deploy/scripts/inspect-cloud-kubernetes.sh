#!/bin/bash
command -v kubectl || true
command -v k3s || true
ls /etc/kubernetes /etc/rancher/k3s 2>/dev/null || true
docker ps --format '{{.Names}}'
kubectl get nodes --request-timeout=10s 2>&1 || true
kubectl get svc -A --request-timeout=10s 2>&1 || true
