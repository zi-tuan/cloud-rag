#!/bin/bash
set -eu
sed -i 's/--no-tools/--no-builtin-tools/' /home/uav/uav-agent-mvp/bin/pi
