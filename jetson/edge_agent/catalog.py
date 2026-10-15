"""登记服务目录；Harbor 中存在镜像并不等于允许 Agent 使用。"""
import copy
import json
import re
import time
from pathlib import Path

from shared.communication.envelope import ProtocolError, SUITES


IMAGE = re.compile(r'47\.109\.49\.98:30881/uav-mvp/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}\Z')


class ServiceCatalog:
    def __init__(self, document):
        if document.get('version') != 1 or not isinstance(document.get('services'), list):
            raise ProtocolError('unsupported service catalog')
        self.entries = {}
        for item in document['services']:
            if item['id'] in self.entries or item['suite'] not in SUITES or item['architecture'] != 'arm64' or item['namespace'] != 'uav-mvp' or item['interface_version'] != 'v1':
                raise ProtocolError('invalid registered service')
            if not IMAGE.fullmatch(item['image']) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', item['deployment']):
                raise ProtocolError('service must use an allowed registry and immutable digest')
            if item.get('replicas') != 1 or item.get('health_path') != '/ready':
                raise ProtocolError('invalid service activation bounds')
            self.entries[item['id']] = copy.deepcopy(item)

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text()))

    def get(self, service_id, *, for_activation=False):
        if service_id not in self.entries:
            raise ProtocolError('service is not registered')
        entry = self.entries[service_id]
        if for_activation and entry.get('registry_verified') is not True:
            raise ProtocolError('Harbor push/pull provenance has not been verified')
        return copy.deepcopy(entry)

    def public(self):
        return [copy.deepcopy(value) for value in self.entries.values()]

    def for_suite(self, suite):
        matches = [entry for entry in self.entries.values() if entry['suite'] == suite]
        if len(matches) != 1:
            raise ProtocolError('suite must identify exactly one registered service')
        return copy.deepcopy(matches[0])


class GuardedOrchestrator:
    """通过 MCP 从登记模板创建服务；模型只选择 service_id。"""
    def __init__(self, catalog, mcp_call, *, writes_enabled=False, templates=None):
        self.catalog, self.mcp_call, self.writes_enabled = catalog, mcp_call, writes_enabled
        self.templates = copy.deepcopy(templates or {})

    def resource(self, api_version, kind, namespace, name):
        import yaml
        result = self.mcp_call('resources_get', {'apiVersion': api_version, 'kind': kind,
                                                'namespace': namespace, 'name': name})
        blocks = result.get('content', [])
        if result.get('isError'):
            # 只有 Kubernetes 明确 NotFound 才意味着未创建。Forbidden/超时不能触发写操作。
            plural = {'Deployment': 'deployments.apps', 'Service': 'services', 'ConfigMap': 'configmaps'}[kind]
            expected = 'failed to get resource: ' + plural + ' "' + name + '" not found'
            if len(blocks) == 1 and blocks[0].get('text', '').strip() == expected:
                return None
            raise ProtocolError('MCP resource read failed; reconcile before creating')
        if len(blocks) != 1 or blocks[0].get('type') != 'text':
            raise ProtocolError('unexpected MCP resource response')
        resource = yaml.safe_load(blocks[0]['text'])
        if not isinstance(resource, dict) or resource.get('kind') != kind or resource.get('apiVersion') != api_version:
            raise ProtocolError('unexpected resource kind')
        if resource.get('metadata', {}).get('name') != name or resource['metadata'].get('namespace') != namespace:
            raise ProtocolError('MCP resource identity mismatch')
        return resource

    def deployment(self, entry):
        resource = self.resource('apps/v1', 'Deployment', entry['namespace'], entry['deployment'])
        if resource is not None:
            self.validate_deployment(entry, resource)
        return resource

    @staticmethod
    def validate_deployment(entry, resource):
        metadata, spec = resource['metadata'], resource['spec']
        if metadata['name'] != entry['deployment'] or metadata['namespace'] != entry['namespace']:
            raise ProtocolError('MCP resource identity mismatch')
        if spec.get('replicas') not in (0, 1):
            raise ProtocolError('deployment replica count exceeds registered bounds')
        pod = spec['template']['spec']
        containers = pod['containers']
        if len(containers) != 1 or containers[0]['name'] != entry.get('container_name', 'crypto') or containers[0]['image'] != entry['image']:
            raise ProtocolError('deployment image differs from registered immutable version')
        security = containers[0].get('securityContext', {})
        if security.get('allowPrivilegeEscalation') is not False or security.get('readOnlyRootFilesystem') is not True or security.get('privileged', False) or security.get('capabilities', {}).get('drop') != ['ALL']:
            raise ProtocolError('deployment container security drift')
        if pod.get('automountServiceAccountToken') is not False or pod.get('hostNetwork', False) or pod.get('hostPID', False) or pod.get('hostIPC', False) or pod.get('initContainers'):
            raise ProtocolError('deployment pod security drift')
        if pod.get('securityContext', {}).get('runAsUser', 0) <= 0:
            raise ProtocolError('deployment must run as non-root')
        if containers[0].get('resources') != entry['resources'] or containers[0].get('readinessProbe', {}).get('httpGet', {}).get('path') != entry['health_path']:
            raise ProtocolError('deployment health/resources differ from catalog')
        return resource

    def status(self, service_id):
        entry = self.catalog.get(service_id)
        resource = self.deployment(entry)
        if resource is None:
            return {'service_id': service_id, 'deployment': entry['deployment'], 'image': entry['image'],
                    'exists': False, 'ready': False, 'generation': None,
                    'registry_verified': entry.get('registry_verified') is True}
        status = resource.get('status', {})
        ready = (status.get('observedGeneration', 0) >= resource['metadata'].get('generation', 1)
                 and resource['spec'].get('replicas') == 1 and status.get('readyReplicas', 0) == 1
                 and status.get('updatedReplicas', 0) == 1 and status.get('availableReplicas', 0) == 1)
        return {'service_id': service_id, 'deployment': entry['deployment'], 'image': entry['image'],
                'exists': True, 'ready': ready, 'generation': resource['metadata'].get('generation'),
                'registry_verified': entry.get('registry_verified') is True}

    @staticmethod
    def matches(expected, actual):
        """API 默认字段可额外存在；模板声明的字段必须全部一致。"""
        if isinstance(expected, dict):
            return isinstance(actual, dict) and all(key in actual and GuardedOrchestrator.matches(value, actual[key])
                                                   for key, value in expected.items())
        if isinstance(expected, list):
            return isinstance(actual, list) and len(expected) == len(actual) and all(
                GuardedOrchestrator.matches(a, b) for a, b in zip(expected, actual))
        return expected == actual

    def provision(self, service_id, entry):
        resources = self.templates.get(service_id)
        if not resources:
            raise ProtocolError('registered creation template is missing')
        if [item.get('kind') for item in resources] != ['ConfigMap', 'Service', 'Deployment']:
            raise ProtocolError('invalid creation template resource set')
        # 所有模板先校验再写入；模板是部署者提供的只读配置，不来自模型参数。
        pending = []
        for item in resources:
            kind, metadata = item['kind'], item['metadata']
            if metadata['namespace'] != entry['namespace'] or 'status' in item:
                raise ProtocolError('creation template scope mismatch')
            if kind in ('Service', 'Deployment') and metadata['name'] != entry['deployment']:
                raise ProtocolError('creation template name mismatch')
            if kind == 'Deployment':
                self.validate_deployment(entry, item)
                if item['spec']['replicas'] != 1:
                    raise ProtocolError('creation template replicas mismatch')
            current = self.resource(item['apiVersion'], kind, metadata['namespace'], metadata['name'])
            if current is None:
                pending.append(item)
            elif not self.matches(item, current):
                raise ProtocolError('existing resource differs from registered template')
        for item in pending:
            result = self.mcp_call('resources_create_or_update', {'resource': json.dumps(item)})
            if result.get('isError'):
                raise ProtocolError('MCP creation failed; reconcile partial resources before retrying')

    def activate(self, service_id):
        if not self.writes_enabled:
            raise ProtocolError('MCP writes are disabled until deployment permissions are hardened')
        entry = self.catalog.get(service_id, for_activation=True)
        resource = self.deployment(entry)
        if resource is None:
            self.provision(service_id, entry)
            return self.status(service_id)
        result = self.mcp_call('resources_scale', {'apiVersion': 'apps/v1', 'kind': 'Deployment',
                                                   'namespace': entry['namespace'], 'name': entry['deployment'], 'scale': 1})
        if result.get('isError'):
            raise ProtocolError('MCP activation failed')
        return self.status(service_id)

    def deactivate(self, service_id):
        """只供确定性任务执行器在切换终态后调用，不暴露给模型。"""
        if not self.writes_enabled:
            raise ProtocolError('MCP writes are disabled until deployment permissions are hardened')
        entry = self.catalog.get(service_id, for_activation=True)
        resource = self.deployment(entry)
        if resource is not None and resource['spec'].get('replicas') != 0:
            result = self.mcp_call('resources_scale', {'apiVersion': 'apps/v1', 'kind': 'Deployment',
                                                       'namespace': entry['namespace'], 'name': entry['deployment'], 'scale': 0})
            if result.get('isError'):
                raise ProtocolError('MCP deactivation failed')
        deadline = time.monotonic() + 120
        while True:
            resource = self.deployment(entry)
            status = {} if resource is None else resource.get('status', {})
            if (resource is None or
                    (resource['spec'].get('replicas') == 0
                     and status.get('observedGeneration', 0) >= resource['metadata'].get('generation', 1)
                     and all(status.get(key, 0) == 0 for key in ('replicas', 'readyReplicas',
                                                                  'updatedReplicas', 'availableReplicas')))):
                pods = self.mcp_call('pods_list_in_namespace', {'namespace': entry['namespace'],
                                                                 'labelSelector': 'app=' + entry['deployment']})
                blocks = pods.get('content', [])
                if pods.get('isError') or len(blocks) != 1 or blocks[0].get('type') != 'text':
                    raise ProtocolError('MCP Pod listing failed; reconcile before task completion')
                listing = blocks[0]['text'].strip()
                if not listing:
                    return {'service_id': service_id, 'stopped': True}
                if not listing.startswith('NAMESPACE'):
                    raise ProtocolError('unexpected MCP Pod listing; reconcile before task completion')
            if time.monotonic() >= deadline:
                raise ProtocolError('crypto Pod did not exit; reconcile before task completion')
            time.sleep(1)
