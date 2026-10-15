#!/bin/bash
set -u
id
date -u
printf '\nConfiguration paths\n'
for path in /home/uav /home/uav/.kube /home/uav/.kube/config /home/uav/.kube/k3s.yaml /etc/rancher/k3s /etc/rancher/k3s/k3s.yaml /home/uav/uav-agent-mvp/mcp/kubeconfig; do
  ls -ld "$path" 2>&1 || true
done
printf '\nNoninteractive sudo\n'
if sudo -n true 2>&1; then printf 'sudo_available=true\n'; else printf 'sudo_available=false\n'; fi
printf '\nService status\n'
systemctl is-active k3s
printf '\nCredential metadata\n'
python3 - <<'PY'
import base64, datetime, json, os, subprocess
import yaml
for path in ['/home/uav/.kube/config', '/home/uav/.kube/k3s.yaml', '/etc/rancher/k3s/k3s.yaml', '/home/uav/uav-agent-mvp/mcp/kubeconfig']:
    print('path:', path)
    try:
        with open(path) as f:
            config = yaml.safe_load(f)
        for item in config.get('users', []):
            user = item.get('user', {})
            print('credential_types:', ','.join(user.keys()))
            if user.get('client-certificate-data'):
                subprocess.run(['openssl', 'x509', '-noout', '-dates'], input=base64.b64decode(user['client-certificate-data']))
            token = user.get('token', '')
            if token.count('.') == 2:
                part = token.split('.')[1]
                payload = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
                for key in ['iat', 'nbf', 'exp']:
                    if key in payload:
                        print(key, datetime.datetime.utcfromtimestamp(payload[key]).isoformat() + 'Z')
    except Exception as exc:
        print(type(exc).__name__)
PY
printf '\nDefault admin access\n'
export KUBECONFIG=/home/uav/.kube/config
k3s kubectl --request-timeout=10s get nodes
k3s kubectl --request-timeout=10s auth can-i '*' '*'
k3s kubectl --request-timeout=10s get deployments -n uav-mvp
k3s kubectl --request-timeout=10s get serviceaccounts,rolebindings -n uav-mvp
