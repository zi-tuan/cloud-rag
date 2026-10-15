#!/bin/bash
set -eu
install -d -m 700 -o uav -g uav /home/uav/uav-agent-mvp/pi-home /home/uav/uav-agent-mvp/project
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- tar -C /home/node -cf - . | tar -xf - -C /home/uav/uav-agent-mvp/pi-home
chown -R uav:uav /home/uav/uav-agent-mvp/pi-home
install -m 600 -o uav -g uav /home/uav/uav-agent-mvp/jetson/pi_runner/config/mcp-project.json /home/uav/uav-agent-mvp/project/.mcp.json
cat > /home/uav/uav-agent-mvp/bin/pi <<'SH'
#!/bin/sh
exec node /workspace/pi/packages/coding-agent/dist/cli.js --no-builtin-tools "$@"
SH
chmod 755 /home/uav/uav-agent-mvp/bin/pi
k3s kubectl apply -f /home/uav/uav-agent-mvp/deploy/jetson/pi-runtime.yaml
k3s kubectl rollout status deployment/pi-runtime -n uav-mvp --timeout=60s
k3s kubectl exec -n uav-mvp deployment/pi-runtime -- /opt/uav/bin/pi --version
