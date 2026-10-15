#!/bin/bash
set -eu
echo '--- user unit ---'
systemctl --user status uav-cloud-tunnel --no-pager -l || true
echo '--- user unit logs ---'
journalctl --user -u uav-cloud-tunnel -n 30 --no-pager || true
echo '--- system unit logs ---'
journalctl -u uav-cloud-tunnel -n 20 --no-pager || true
echo '--- process ---'
ps -eo pid,stat,comm,args | grep -E '[n]ps|[n]pc|[s]sh .*15443|[s]sh .*18443|[c]loud-tunnel' || true
echo '--- socket connections ---'
ss -tnp | grep -E '15443|18443|47\.109\.49\.98|24531' || true
