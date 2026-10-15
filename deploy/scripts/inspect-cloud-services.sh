#!/bin/bash
set -eu
hostname
systemctl list-units --type=service --state=running --no-pager | grep -Ei 'rabbit|mysql|nps|npc|uav|crypto|nginx' || true
ss -lntp | grep -E ':3088[0-9]|:40000|:5672|:80 |:443 ' || true
docker ps --format '{{.Names}} {{.Image}} {{.Ports}}'
find /home/uav/cloud-net-end -maxdepth 2 -type d | head -30
