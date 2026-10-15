#!/bin/bash
set -eu
id
for path in /home/uav/uav-agent-mvp/control /home/uav/uav-agent-mvp/tunnel /home/uav/uav-agent-mvp/project /home/uav/uav-agent-mvp/pi-home; do
  if [ -e "$path" ]; then
    stat -c '%A %U:%G %n' "$path"
    find "$path" -maxdepth 2 -type f -printf '%m %u:%g %s %p\n' 2>/dev/null | sed -E 's#(/keys/)[^/]+#\1[REDACTED]#g'
  else
    echo "missing $path"
  fi
done
systemctl --user is-active uav-edge-agent 2>/dev/null || true
systemctl is-active uav-cloud-tunnel 2>/dev/null || true
ss -lnt 2>/dev/null | grep -E ':18443|:18444|:18081|:18082' || true
python3 - <<'PY'
import json
from pathlib import Path
path = Path('/home/uav/uav-agent-mvp/project/.mcp.json')
if path.is_file():
    data = json.loads(path.read_text())
    print('mcp_servers=' + ','.join(sorted(data.get('mcpServers', {}))))
    for name, entry in data.get('mcpServers', {}).items():
        print(name + '_command=' + str(entry.get('command', '')))
else:
    print('mcp_config=missing')
PY
