"""模块隔离与新镜像渲染契约，避免包移动后留下不可部署的登记或导入。"""
import json
from pathlib import Path
import tempfile
import unittest

import yaml
from fastapi.testclient import TestClient
from unittest.mock import Mock

from deploy.scripts.build_offline_images import MODULES, ROOT, layout
from deploy.scripts.render_layout import render
from jetson.edge_agent.auth_lifecycle import AuthenticationCatalog, AuthenticationOrchestrator
from jetson.edge_agent.catalog import ServiceCatalog, GuardedOrchestrator
from services.auth_service.app import create_app
from shared.communication.auth_client import AuthServiceClient
from shared.communication.envelope import ProtocolError


class ServiceLayoutTests(unittest.TestCase):
    def test_runtime_images_use_real_entries_and_exclude_other_deployment_roles(self):
        for module in MODULES:
            spec = layout(module)
            self.assertTrue(spec['entrypoint'][-1].startswith(('cloud.', 'jetson.', 'services.')))
            self.assertTrue(all((ROOT / source).exists() for source, _ in spec['copies']))
            if module == 'cloud-api':
                self.assertFalse(any(source.startswith('jetson/') for source, _ in spec['copies']))
            if module in ('crypto-service', 'auth-service'):
                self.assertFalse(any(source.startswith(('jetson/', 'cloud/')) for source, _ in spec['copies']))

    def test_renderer_keeps_images_catalogs_and_pod_isolation_in_sync(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for module, (_, architectures) in MODULES.items():
                for architecture in architectures:
                    record = {'module': module, 'architecture': architecture,
                              'image': '47.109.49.98:30881/uav-mvp/' + module + ':test', 'digest': 'sha256:' + '1' * 64}
                    (directory / f'{module}-{architecture}.json').write_text(json.dumps(record), encoding='utf-8')
            output = directory / 'rendered'
            render(directory, output)
            documents = list(yaml.safe_load_all((output / 'jetson.yaml').read_text(encoding='utf-8')))
            cm = next(item for item in documents if item['kind'] == 'ConfigMap' and item['metadata']['name'] == 'uav-service-registry')
            catalog = ServiceCatalog(json.loads(cm['data']['service-catalog.json']))
            with self.assertRaises(ProtocolError):
                catalog.get('aes-gcm-v1', for_activation=True)
            templates = json.loads(cm['data']['service-templates.json'])
            for service_id, items in templates.items():
                deployment = next(item for item in items if item['kind'] == 'Deployment')
                GuardedOrchestrator.validate_deployment(catalog.get(service_id), deployment)
            control = next(item for item in documents if item['kind'] == 'ConfigMap' and item['metadata']['name'] == 'uav-edge-control-config')
            auth = AuthenticationCatalog(json.loads(control['data']['mcp-guard.json'])['authentication_deployments'])
            for policy in ('Ed25519', 'RSA', 'SM2'):
                entry = auth.get(policy)
                deployment = next(item for item in documents if item['kind'] == 'Deployment' and item['metadata']['name'] == entry['deployment'])
                AuthenticationOrchestrator.validate_deployment(entry, deployment)
            names = {item['metadata']['name']: item for item in documents if item['kind'] == 'Deployment'}
            self.assertIn('/pi-runner@', names['pi-runner']['spec']['template']['spec']['containers'][0]['image'])
            self.assertIn('/edge-agent@', names['uav-edge-agent']['spec']['template']['spec']['containers'][0]['image'])
            edge_pod = names['uav-edge-agent']['spec']['template']['spec']
            self.assertNotIn('auth-client', {volume['name'] for volume in edge_pod['volumes']})
            self.assertTrue({'auth-ed25519-client', 'auth-rsa-client', 'auth-sm2-client'} <= {volume['name'] for volume in edge_pod['volumes']})


class AuthenticationRequestTests(unittest.TestCase):
    def test_strict_shared_request_models_match_http_and_openapi(self):
        provider = Mock(policy='Ed25519')
        provider.prove.return_value = 'proof'
        token = 'x' * 32
        with TestClient(create_app(provider, token)) as client:
            headers = {'Authorization': 'Bearer ' + token}
            body = {'purpose': 'cloud-challenge', 'transcript': {}, 'envelope': None}
            self.assertEqual(client.post('/v1/prove', json=body, headers=headers).json(), {'proof': 'proof'})
            for invalid in ({**body, 'extra': True}, {**body, 'purpose': 123}, {**body, 'transcript': []}, {'purpose': 'cloud-challenge'}):
                self.assertEqual(client.post('/v1/prove', json=invalid, headers=headers).status_code, 400)
            schema = client.get('/openapi.json').json()
            self.assertNotIn('422', schema['paths']['/v1/prove']['post']['responses'])
            self.assertIn('400', schema['paths']['/v1/prove']['post']['responses'])
            self.assertFalse(schema['components']['schemas']['ProofRequest']['additionalProperties'])

    def test_invalid_client_request_is_rejected_before_network_access(self):
        client = AuthServiceClient('http://127.0.0.1:8080', 'x' * 32)
        client.request = Mock()
        with self.assertRaises(ProtocolError):
            client.prove(1, {})
        with self.assertRaises(ProtocolError):
            client.verify('cloud-challenge', {}, 1)
        client.request.assert_not_called()
