#!/bin/bash
k3s kubectl get pods -n uav-mvp -o wide
k3s kubectl get events -n uav-mvp --sort-by=.lastTimestamp | tail -12
k3s kubectl logs job/crypto-arm64-verification -n uav-mvp --tail=30 || true
