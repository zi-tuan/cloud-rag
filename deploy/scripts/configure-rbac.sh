#!/bin/bash
set -eu
k3s kubectl apply -f /home/uav/uav-agent-mvp/deploy/infrastructure/k3s/namespace-rbac.yaml
install -d -m 700 -o uav -g uav /home/uav/uav-agent-mvp/mcp
umask 077
cfg=/home/uav/uav-agent-mvp/mcp/kubeconfig
token=$(k3s kubectl create token uav-agent -n uav-mvp --duration=24h)
k3s kubectl config --kubeconfig="$cfg" set-cluster local --server=https://192.168.8.101:6443 --certificate-authority=/var/lib/rancher/k3s/server/tls/server-ca.crt --embed-certs=true >/dev/null
k3s kubectl config --kubeconfig="$cfg" set-credentials uav-agent --token="$token" >/dev/null
unset token
k3s kubectl config --kubeconfig="$cfg" set-context uav-agent --cluster=local --user=uav-agent --namespace=uav-mvp >/dev/null
k3s kubectl config --kubeconfig="$cfg" use-context uav-agent >/dev/null
chown uav:uav "$cfg"
chmod 600 "$cfg"
for action in 'get pods -n uav-mvp' 'get secrets -n uav-mvp' 'get pods -n kube-system' 'patch deployment/aes-service -n uav-mvp' 'patch deployment/pi-agent -n uav-mvp' 'create pods/exec -n uav-mvp'; do
  printf '%s: ' "$action"
  k3s kubectl --kubeconfig="$cfg" auth can-i $action || true
done
printf 'Bootstrap token expires in 24h; replace with projected service account credentials for the Pi runtime.\n'
