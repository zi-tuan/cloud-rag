#!/bin/bash
set -euo pipefail

base=/home/uav/uav-agent-mvp/control
for path in "$base" "$base/edge" "$base/pi-runner" "$base/pi-socket"; do
  if [ -e "$path" ]; then
    owner=$(stat -c '%u:%g' "$path")
    if [ "$owner" != '1000:1000' ]; then
      echo "Refusing to change existing path with unexpected owner: $path" >&2
      exit 1
    fi
    if [ ! -d "$path" ]; then
      echo "Expected a directory: $path" >&2
      exit 1
    fi
  else
    install -d -o 1000 -g 1000 -m 0700 "$path"
  fi
done
echo 'Edge control directories are owned by UID/GID 1000.'
