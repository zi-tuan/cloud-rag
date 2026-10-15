#!/bin/bash
set -eu
ps -C python3 -o pid,etime,stat,%cpu,%mem,comm,args | grep -E 'verify_harbor_jetson_oci|PID' || true
ss -tinp | grep -A2 -B1 '127\.0\.0\.1:15443' || true
test -f /home/uav/uav-agent-mvp/deploy/harbor-jetson-pull-verification.json && cat /home/uav/uav-agent-mvp/deploy/harbor-jetson-pull-verification.json || true
