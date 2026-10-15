#!/bin/bash
set -eu
/home/uav/uav-agent-mvp/bin/kubernetes-mcp-server --help
k3s kubectl auth can-i list pods -n uav-mvp --as=system:serviceaccount:uav-mvp:uav-agent
k3s kubectl auth can-i list deployments.apps -n uav-mvp --as=system:serviceaccount:uav-mvp:uav-agent
k3s kubectl get role uav-agent -n uav-mvp -o yaml
ls -la /home/uav/uav-agent-mvp/project
