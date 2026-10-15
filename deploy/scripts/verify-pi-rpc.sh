#!/bin/bash
set -eu
printf '%s\n' '{"id":"check","type":"get_commands"}' | timeout 25s k3s kubectl exec -i -n uav-mvp deployment/pi-runtime -- /opt/uav/bin/pi --mode rpc --no-session
