#!/bin/bash
set -eu
mkdir -p /home/uav/uav-agent-mvp/artifacts
cd /home/uav/uav-agent-mvp/artifacts
curl -fL --connect-timeout 10 --max-time 120 https://getaivo.dev/dl/v0.49.9/aivo-linux-arm64 -o aivo-linux-arm64
printf '%s\n' '5a87c7a76dfba0700a4103aa42bbd7f16df25c7c92d06ad950644725141afb13  aivo-linux-arm64' | sha256sum -c -
