"""Generate reviewed Kubernetes JSON (accepted by kubectl); never contacts a cluster."""
import argparse
import json
from pathlib import Path
import re

def render(namespace, image, claim, release, architecture='amd64'):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', namespace) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', claim):
        raise ValueError('explicit valid namespace and existing PVC required')
    if not re.fullmatch(r'[a-f0-9]{16}', release):
        raise ValueError('release_id from verified package required')
    if not re.fullmatch(r'.+@sha256:[a-f0-9]{64}', image):
        raise ValueError('immutable uploaded image digest required')
    labels = {'app': 'uav-rag'}
    probe = {'httpGet': {'path': '/health', 'port': 8787}, 'periodSeconds': 10}
    pod = {'automountServiceAccountToken': False,
           'nodeSelector': {'kubernetes.io/arch': architecture},
           'securityContext': {'runAsUser': 1000, 'runAsGroup': 1000, 'fsGroup': 1000},
           'containers': [{'name': 'rag', 'image': image, 'imagePullPolicy': 'IfNotPresent',
               'ports': [{'containerPort': 8787}],
               'env': [{'name': 'UAV_RAG_TOKEN_FILE', 'value': '/rag-secret/service.token'}],
               'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                   'capabilities': {'drop': ['ALL']}},
               'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'cpu': '1', 'memory': '1Gi'}},
               'readinessProbe': probe, 'livenessProbe': {**probe, 'initialDelaySeconds': 30},
               'volumeMounts': [{'name': 'knowledge', 'mountPath': '/data', 'subPath': release, 'readOnly': True},
                                {'name': 'token', 'mountPath': '/rag-secret', 'readOnly': True}]}],
           'volumes': [{'name': 'knowledge', 'persistentVolumeClaim': {'claimName': claim}},
                       {'name': 'token', 'secret': {'secretName': 'uav-rag-token', 'defaultMode': 0o440}}]}
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [
        {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': 'uav-rag', 'namespace': namespace},
         'spec': {'replicas': 1, 'revisionHistoryLimit': 3, 'strategy': {'type': 'Recreate'},
                  'selector': {'matchLabels': labels}, 'template': {'metadata': {'labels': labels,
                  'annotations': {'uav/knowledge-release': release}}, 'spec': pod}}},
        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'uav-rag', 'namespace': namespace},
         'spec': {'type': 'ClusterIP', 'selector': labels, 'ports': [{'port': 8787, 'targetPort': 8787}]}}]}

def main():
    p = argparse.ArgumentParser()
    for name in ('namespace', 'image', 'claim', 'release'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--architecture', choices=['amd64', 'arm64'], default='amd64')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    document = render(args.namespace, args.image, args.claim, args.release, args.architecture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2)+'\n', encoding='utf-8')

if __name__ == '__main__':
    main()
