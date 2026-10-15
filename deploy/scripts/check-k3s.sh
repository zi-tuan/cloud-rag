#!/bin/bash
set -eu
chmod 755 /home/uav/uav-agent-mvp/bin/k3s
/home/uav/uav-agent-mvp/bin/k3s --version
/home/uav/uav-agent-mvp/bin/k3s check-config
