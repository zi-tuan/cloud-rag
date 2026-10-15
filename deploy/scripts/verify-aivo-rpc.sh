#!/bin/bash
set -eu
printf '%s\n' '{"id":"check","type":"get_commands"}' | timeout 40s k3s kubectl exec -i -n uav-mvp deployment/pi-runtime -- env PATH=/opt/uav/bin:/usr/local/bin:/usr/bin:/bin /opt/uav/bin/aivo pi --model mdoelscope1::Qwen/Qwen3.5-35B-A3B -- --mode rpc --no-session
