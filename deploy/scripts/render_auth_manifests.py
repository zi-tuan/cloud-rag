"""生成旁路认证 Deployment，默认零副本；注册公钥并验证 ARM64 后启用。"""
import argparse
import json
from pathlib import Path


def manifests(image, config, namespace='uav-mvp'):
    name = 'auth-' + config.get('policy', 'Ed25519').lower()
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name + '-config', 'namespace': namespace},
         'data': {'auth.json': json.dumps(config)}},
        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': name, 'namespace': namespace},
         'spec': {'selector': {'app': name}, 'ports': [{'port': 8080, 'targetPort': 8080}]}},
        {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': name, 'namespace': namespace},
         'spec': {'replicas': 0, 'selector': {'matchLabels': {'app': name}}, 'template': {
             'metadata': {'labels': {'app': name}}, 'spec': {
                 'automountServiceAccountToken': False,
                 'securityContext': {'runAsUser': 1000, 'runAsGroup': 1000, 'fsGroup': 1000, 'runAsNonRoot': True},
                 'containers': [{'name': 'auth', 'image': image, 'imagePullPolicy': 'Never',
                                 'command': ['python', '-m', 'services.auth_service'],
                                 'env': [{'name': 'UAV_AUTH_CONFIG', 'value': '/config/auth.json'}],
                                 'ports': [{'containerPort': 8080}],
                                 'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                                     'capabilities': {'drop': ['ALL']}},
                                 'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'},
                                               'limits': {'cpu': '500m', 'memory': '192Mi'}},
                                 'readinessProbe': {'exec': {'command': ['python', '-c',
                                     "import json,urllib.request;from pathlib import Path;"
                                     "r=urllib.request.Request('http://127.0.0.1:8080/ready',headers={'Authorization':'Bearer '+Path('/identity/service.token').read_text().strip()});"
                                     "assert json.load(urllib.request.urlopen(r,timeout=2))['ready']"]},
                                                    'initialDelaySeconds': 3, 'periodSeconds': 10, 'timeoutSeconds': 5},
                                 'livenessProbe': {'httpGet': {'path': '/health', 'port': 8080},
                                                   'initialDelaySeconds': 10, 'periodSeconds': 15},
                                 'volumeMounts': [{'name': 'config', 'mountPath': '/config', 'readOnly': True},
                                                  {'name': 'identity', 'mountPath': '/identity', 'readOnly': True},
                                                  {'name': 'scratch', 'mountPath': '/tmp'}]}],
                 'volumes': [{'name': 'config', 'configMap': {'name': name + '-config'}},
                             {'name': 'identity', 'secret': {'secretName': 'uav-' + name + '-identity', 'defaultMode': 0o440}},
                             {'name': 'scratch', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '16Mi'}}]
             }}}},
        {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
         'metadata': {'name': name + '-ingress', 'namespace': namespace},
         'spec': {'podSelector': {'matchLabels': {'app': name}}, 'policyTypes': ['Ingress'],
                  'ingress': [{'from': [{'podSelector': {'matchLabels': {'app': 'uav-edge-agent'}}}],
                               'ports': [{'protocol': 'TCP', 'port': 8080}]}]}}
    ]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--image', required=True, help='经核验的认证服务镜像引用')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    Path(args.output).write_text(json.dumps(manifests(args.image, config), indent=2) + '\n')
