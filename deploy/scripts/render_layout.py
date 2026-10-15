"""从独立模块镜像锁渲染云端/Jetson 清单及运行时登记；只写本地输出。"""
import argparse
import copy
import json
from pathlib import Path
import re

import yaml

from deploy.scripts.build_offline_images import MODULES, ROOT, CACHE
from deploy.scripts.render_auth_manifests import manifests as authentication_manifests


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def yaml_text(documents):
    class Dumper(yaml.SafeDumper):
        pass

    def string(dumper, value):
        return dumper.represent_scalar('tag:yaml.org,2002:str', value, style='|' if '\n' in value else None)

    Dumper.add_representer(str, string)
    return yaml.dump_all(documents, Dumper=Dumper, sort_keys=False, allow_unicode=True)


def image(lock_dir, module, architecture):
    record = load(lock_dir / f'{module}-{architecture}.json')
    if record['module'] != module or record['architecture'] != architecture:
        raise ValueError('image lock module or architecture mismatch')
    digest = record['digest']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('immutable image digest required')
    return record['image'].rsplit(':', 1)[0] + '@' + digest


def render(lock_dir, output, *, registry_verified=False, auth_config_dir=None, rag_url='https://RAG_HOST_REPLACE'):
    images = {(module, architecture): image(lock_dir, module, architecture)
              for module, (_, architectures) in MODULES.items() for architecture in architectures}
    cloud = list(yaml.safe_load_all((ROOT / 'deploy/cloud/cloud-control-k8s.yaml').read_text(encoding='utf-8')))
    edge = list(yaml.safe_load_all((ROOT / 'deploy/jetson/edge-control-k8s.yaml').read_text(encoding='utf-8')))
    from deploy.scripts.rag_config import configure
    configure(edge, rag_url)
    catalog_dir = ROOT / 'jetson/edge_agent/config'
    catalog = load(catalog_dir / 'service-catalog.json')
    templates = load(catalog_dir / 'service-templates.json')
    authentication = load(catalog_dir / 'authentication-deployments.json')
    for entry in catalog['services']:
        entry.update(image=images['crypto-service', 'arm64'], registry_verified=registry_verified)
    for resources in templates.values():
        for item in resources:
            if item['kind'] == 'Deployment':
                item['spec']['template']['spec']['containers'][0]['image'] = images['crypto-service', 'arm64']

    auth_resources = {'edge': [], 'cloud': []}
    for role, namespace, architecture in (('edge', 'uav-mvp', 'arm64'), ('cloud', 'uav', 'amd64')):
        for policy in ('Ed25519', 'RSA', 'SM2'):
            path = (auth_config_dir / role / (policy.lower() + '.json') if auth_config_dir else
                    ROOT / 'services/auth_service/config' / (policy.lower() + '.example.json'))
            config = load(path)
            config.update(role=role, policy=policy, bind='0.0.0.0', port=8080)
            resources = authentication_manifests(images['auth-service', architecture], config, namespace)['items']
            deployment = next(item for item in resources if item['kind'] == 'Deployment')
            pod = deployment['spec']['template']['spec']
            pod['nodeSelector'] = {'kubernetes.io/arch': architecture}
            deployment['spec']['replicas'] = 1 if role == 'cloud' or policy == 'Ed25519' else 0
            if role == 'edge':
                # 生成登记模板和实际 Pod 的同一份隔离配置，避免启停时模板漂移。
                authentication[policy].update(image=images['auth-service', architecture],
                                              pod_spec=copy.deepcopy(pod), registry_verified=registry_verified)
            else:
                for item in resources:
                    if item['kind'] == 'NetworkPolicy':
                        item['spec']['ingress'][0]['from'] = [{'podSelector': {'matchLabels': {'app': 'uav-cloud-api'}}}]
            auth_resources[role].extend(resources)

    registration = {'apiVersion': 'v1', 'kind': 'ConfigMap',
                    'metadata': {'name': 'uav-service-registry', 'namespace': 'uav-mvp'},
                    'data': {'service-catalog.json': json.dumps(catalog), 'service-templates.json': json.dumps(templates)}}
    for documents, role, architecture in ((cloud, 'cloud', 'amd64'), (edge, 'edge', 'arm64')):
        configmap = next(item for item in documents if item['kind'] == 'ConfigMap')
        data = configmap['data']
        key = 'api.json' if role == 'cloud' else 'edge.json'
        config = json.loads(data[key])
        config['identity_auth'] = {
            'cloud_id': 'uav-cloud-01', 'credential_version': '1',
            'services': {policy: {'service_url': f'http://auth-{policy.lower()}.{configmap["metadata"]["namespace"]}.svc:8080',
                                 'token_file': f'/auth-{policy.lower()}-client/service.token'}
                         for policy in ('Ed25519', 'RSA', 'SM2')}}
        if role == 'cloud':
            config['identity_auth']['state_path'] = '/state/auth.db'
        else:
            config['catalog'] = '/opt/uav/catalog/service-catalog.json'
            guard = json.loads(data['mcp-guard.json'])
            guard.update(catalog=config['catalog'], templates='/opt/uav/catalog/service-templates.json',
                         authentication_deployments=authentication)
            data['mcp-guard.json'] = json.dumps(guard, indent=2)
            runner = json.loads(data['pi-runner.json'])
            runner['mcp_guard_config'] = '/opt/uav/config/mcp-guard.json'
            data['pi-runner.json'] = json.dumps(runner, indent=2)
        data[key] = json.dumps(config, indent=2)
        for item in documents:
            if item['kind'] != 'Deployment':
                continue
            name = item['metadata']['name']
            module = {'uav-cloud-api': 'cloud-api', 'uav-cloud-aes': 'crypto-service', 'uav-cloud-sm4': 'crypto-service',
                      'pi-runner': 'pi-runner', 'uav-edge-agent': 'edge-agent'}[name]
            pod = item['spec']['template']['spec']
            pod.setdefault('nodeSelector', {})['kubernetes.io/arch'] = architecture
            container = pod['containers'][0]
            container['image'] = images[module, architecture]
            container['command'] = {'cloud-api': ['python', '-m', 'cloud.cloud_api.api'],
                                    'pi-runner': ['python', '-m', 'jetson.pi_runner'],
                                    'edge-agent': ['python', '-m', 'jetson.edge_agent.runtime'],
                                    'crypto-service': ['python', '-m', 'services.crypto_service']}[module]
            if module in ('cloud-api', 'edge-agent'):
                container['volumeMounts'] = [item for item in container['volumeMounts'] if item['name'] != 'auth-client']
                pod['volumes'] = [item for item in pod['volumes'] if item['name'] != 'auth-client']
                for policy in ('ed25519', 'rsa', 'sm2'):
                    volume_name = 'auth-' + policy + '-client'
                    container['volumeMounts'].append({'name': volume_name, 'mountPath': '/' + volume_name, 'readOnly': True})
                    pod['volumes'].append({'name': volume_name, 'secret': {'secretName': 'uav-auth-' + policy + '-client',
                                                                        'defaultMode': 0o440}})
            if role == 'edge':
                container['volumeMounts'].append({'name': 'service-registry', 'mountPath': '/opt/uav/catalog', 'readOnly': True})
                pod['volumes'].append({'name': 'service-registry', 'configMap': {'name': 'uav-service-registry'}})
    crypto_resources = load(ROOT / 'deploy/jetson/crypto-workloads.json')['items']
    for item in crypto_resources:
        if item['kind'] == 'Deployment':
            pod = item['spec']['template']['spec']
            pod.setdefault('nodeSelector', {})['kubernetes.io/arch'] = 'arm64'
            pod['containers'][0]['image'] = images['crypto-service', 'arm64']
    # 激活模板必须与实际部署完全一致。
    for service_id, resources in templates.items():
        name = next(item['metadata']['name'] for item in resources if item['kind'] == 'Deployment')
        for item in resources:
            if item['kind'] == 'Deployment':
                actual = next(value for value in crypto_resources if value['kind'] == 'Deployment' and value['metadata']['name'] == name)
                item['spec']['template']['spec'] = copy.deepcopy(actual['spec']['template']['spec'])
    registration['data']['service-templates.json'] = json.dumps(templates)
    output.mkdir(parents=True, exist_ok=True)
    namespace = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'uav'}}
    rbac = list(yaml.safe_load_all((ROOT / 'deploy/infrastructure/k3s/namespace-rbac.yaml').read_text(encoding='utf-8')))
    (output / 'cloud.yaml').write_text(yaml_text([namespace] + cloud + auth_resources['cloud']), encoding='utf-8')
    (output / 'jetson.yaml').write_text(yaml_text(rbac + [registration] + edge + crypto_resources + auth_resources['edge']), encoding='utf-8')
    (output / 'service-catalog.json').write_text(json.dumps(catalog, indent=2) + '\n', encoding='utf-8')
    print('Rendered cloud.yaml and jetson.yaml; registry_verified=' + str(registry_verified))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock-dir', type=Path, default=CACHE / 'service-layout')
    parser.add_argument('--output', type=Path, default=CACHE / 'service-layout/manifests')
    parser.add_argument('--auth-config-dir', type=Path, help='原主机生成配置的 role/policy.json 目录，仅含路径和公开元数据')
    parser.add_argument('--registry-verified', action='store_true', help='仅在所有新镜像完成登记仓库推拉核验后使用')
    parser.add_argument('--rag-url', required=True, help='Agent Pod 可达的 HTTPS 根地址或同网络命名空间回环隧道')
    args = parser.parse_args()
    render(args.lock_dir, args.output, registry_verified=args.registry_verified, auth_config_dir=args.auth_config_dir, rag_url=args.rag_url)


if __name__ == '__main__':
    main()
