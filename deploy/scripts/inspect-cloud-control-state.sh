#!/bin/bash
set -eu
root=/home/uav/uav-agent-mvp/control
for name in uav-cloud-api uav-cloud-aes uav-cloud-sm4; do
  docker inspect --format '{{.Name}} image={{.Config.Image}} {{range .Mounts}}{{.Source}}:{{.Destination}} {{end}}' "$name"
done
find "$root" -maxdepth 3 -printf '%M %u:%g %s %p\n' 2>/dev/null | sed -E 's#(/keys/)[^/]+#\1[REDACTED]#g'
