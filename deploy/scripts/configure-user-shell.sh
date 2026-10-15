#!/bin/bash
set -eu
root="$HOME/uav-agent-mvp"
mkdir -p "$root/host-bin"
cat > "$root/host-bin/pi" <<'SH'
#!/bin/sh
if [ -t 0 ] && [ -t 1 ]; then
  set -- -it -n uav-mvp deployment/pi-runtime -- env PATH=/opt/uav/bin:/usr/local/bin:/usr/bin:/bin /opt/uav/bin/aivo pi --model sensenova::sensenova-6.8-flash-lite -- "$@"
else
  set -- -i -n uav-mvp deployment/pi-runtime -- env PATH=/opt/uav/bin:/usr/local/bin:/usr/bin:/bin /opt/uav/bin/aivo pi --model sensenova::sensenova-6.8-flash-lite -- "$@"
fi
exec sudo /usr/local/bin/k3s kubectl exec "$@"
SH
cat > "$root/host-bin/pi-edge" <<'SH'
#!/bin/sh
exec "$HOME/uav-agent-mvp/host-bin/pi" "$@"
SH
cat > "$root/host-bin/aivo" <<'SH'
#!/bin/sh
if [ -t 0 ] && [ -t 1 ]; then
  exec sudo /usr/local/bin/k3s kubectl exec -it -n uav-mvp deployment/pi-runtime -- env PATH=/opt/uav/bin:/usr/local/bin:/usr/bin:/bin /opt/uav/bin/aivo "$@"
else
  exec sudo /usr/local/bin/k3s kubectl exec -i -n uav-mvp deployment/pi-runtime -- env PATH=/opt/uav/bin:/usr/local/bin:/usr/bin:/bin /opt/uav/bin/aivo "$@"
fi
SH
chmod 755 "$root/host-bin/pi" "$root/host-bin/pi-edge" "$root/host-bin/aivo"
cat > "$root/env.sh" <<'SH'
export UAV_HOME="$HOME/uav-agent-mvp"
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
case ":$PATH:" in
  *":$UAV_HOME/host-bin:$UAV_HOME/bin:"*) ;;
  *) export PATH="$UAV_HOME/host-bin:$UAV_HOME/bin:$PATH" ;;
esac
SH
if ! grep -Fq '# UAV Edge Agent environment' "$HOME/.bashrc"; then
  cp -p "$HOME/.bashrc" "$HOME/.bashrc.before-uav-env-$(date +%Y%m%d%H%M%S)"
  cat >> "$HOME/.bashrc" <<'SH'

# UAV Edge Agent environment
[ ! -f "$HOME/uav-agent-mvp/env.sh" ] || . "$HOME/uav-agent-mvp/env.sh"
SH
fi
. "$root/env.sh"
command -v pi pi-edge aivo k3s kubernetes-mcp-server
