#!/bin/bash
set -eu
kubectl get deployments uav-cloud-aes uav-cloud-sm4 uav-cloud-api -n uav -o wide
kubectl get pods -n uav -l 'app in (uav-cloud-aes,uav-cloud-sm4,uav-cloud-api)' -o wide
kubectl get services uav-cloud-aes uav-cloud-sm4 uav-cloud-api -n uav -o wide
