import copy
import json
import tempfile
import unittest
from pathlib import Path

from shared.communication.envelope import ProtocolError
from jetson.edge_agent.catalog import GuardedOrchestrator, ServiceCatalog
from jetson.edge_agent.mcp_guard import GuardTools


ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.document = json.loads((ROOT / 'jetson/edge_agent/config/service-catalog.json').read_text())
        workloads = json.loads((ROOT / 'deploy/jetson/crypto-workloads.json').read_text())
        self.resource = next(copy.deepcopy(item) for item in workloads['items']
                             if item['kind'] == 'Deployment' and item['metadata']['name'] == 'sm4-service')
        self.resource['spec']['replicas'] = 1
        self.resource['metadata']['generation'] = 2
        self.resource['status'] = {'observedGeneration': 2, 'readyReplicas': 1, 'updatedReplicas': 1, 'availableReplicas': 1}
        self.calls = []

    def upstream(self, name, arguments):
        self.calls.append((name, arguments))
        return {'content': [{'type': 'text', 'text': json.dumps(self.resource)}]}

    def guard(self, verified=False, writes=False):
        document = copy.deepcopy(self.document)
        for entry in document['services']:
            entry['registry_verified'] = verified
        return GuardedOrchestrator(ServiceCatalog(document), self.upstream, writes_enabled=writes)

    def test_manifest_matches_catalog_and_generation_must_be_observed(self):
        guard = self.guard()
        self.assertTrue(guard.status('sm4-gcm-v1')['ready'])
        self.resource['status']['observedGeneration'] = 1
        self.assertFalse(guard.status('sm4-gcm-v1')['ready'])

    def test_bootstrap_keeps_sm4_idle_but_activation_template_starts_one(self):
        workloads = json.loads((ROOT / 'deploy/jetson/crypto-workloads.json').read_text())
        deployments = {item['metadata']['name']: item for item in workloads['items'] if item['kind'] == 'Deployment'}
        templates = json.loads((ROOT / 'jetson/edge_agent/config/service-templates.json').read_text())
        self.assertEqual(deployments['aes-service']['spec']['replicas'], 1)
        self.assertEqual(deployments['sm4-service']['spec']['replicas'], 0)
        self.assertEqual(templates['sm4-gcm-v1'][-1]['spec']['replicas'], 1)

    def test_unverified_harbor_disabled_writes_and_mutable_images_rejected(self):
        for verified, writes in [(False, True), (True, False)]:
            with self.assertRaises(ProtocolError):
                self.guard(verified, writes).activate('sm4-gcm-v1')
        self.assertEqual(self.calls, [])
        self.document['services'][0]['image'] = '47.109.49.98:30881/uav-mvp/crypto-service:latest'
        with self.assertRaises(ProtocolError):
            ServiceCatalog(self.document)

    def test_image_or_security_drift_prevents_mutation(self):
        original = copy.deepcopy(self.resource)
        for drift in ('image', 'privileged', 'hostNetwork'):
            self.resource = copy.deepcopy(original)
            pod = self.resource['spec']['template']['spec']
            if drift == 'image':
                pod['containers'][0]['image'] = 'unregistered.example/image:latest'
            elif drift == 'privileged':
                pod['containers'][0]['securityContext']['privileged'] = True
            else:
                pod['hostNetwork'] = True
            with self.assertRaises(ProtocolError):
                self.guard(True, True).activate('sm4-gcm-v1')
        self.assertTrue(all(name == 'resources_get' for name, _ in self.calls))

    def test_only_scale_to_one_via_mcp_and_receipt_after_activation(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'selection.json'
            tools = GuardTools(self.guard(True, True), path)
            tools.invoke('uav_service_status', {'service_id': 'sm4-gcm-v1'})
            self.assertFalse(path.exists())
            tools.invoke('uav_service_activate', {'service_id': 'sm4-gcm-v1'})
            receipt = json.loads(path.read_text())
            self.assertEqual(receipt['suite'], 'SM4-GCM')
            self.assertTrue(receipt['ready'])
            with self.assertRaises(ProtocolError):
                tools.invoke('uav_service_activate', {'service_id': 'aes-gcm-v1'})
            reconnected = GuardTools(self.guard(True, True), path)
            with self.assertRaises(ProtocolError):
                reconnected.invoke('uav_service_activate', {'service_id': 'aes-gcm-v1'})
            self.assertEqual(json.loads(path.read_text())['suite'], 'SM4-GCM')
            mutation = [arguments for tool, arguments in self.calls if tool != 'resources_get']
            self.assertEqual(mutation, [{'apiVersion': 'apps/v1', 'kind': 'Deployment',
                                         'namespace': 'uav-mvp', 'name': 'sm4-service', 'scale': 1}])
            for tool, arguments in [('resources_create_or_update', {'resource': '{}'}),
                                    ('uav_service_activate', {'service_id': 'sm4-gcm-v1', 'image': 'evil'}),
                                    ('uav_service_activate', {'service_id': 'unregistered'})]:
                with self.assertRaises(ProtocolError):
                    tools.invoke(tool, arguments)

    def test_activation_can_be_completed_with_agent_authentication_decision(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'selection.json'
            tools = GuardTools(self.guard(True, True), path)
            tools.invoke('uav_service_activate', {'service_id': 'sm4-gcm-v1'})
            tools.invoke('uav_service_status', {'service_id': 'sm4-gcm-v1'})
            target = {'protection_profile': 'lightweight', 'auth_policy': 'RSA', 'suite': 'SM4-GCM'}
            tools.invoke('uav_combination_select', target)
            expected = {'selection_kind': 'combination', 'target': target}
            self.assertEqual(json.loads(path.read_text()), expected)
            tools.invoke('uav_service_status', {'service_id': 'sm4-gcm-v1'})
            tools.invoke('uav_service_activate', {'service_id': 'sm4-gcm-v1'})
            self.assertEqual(json.loads(path.read_text()), expected)
            reconnected = GuardTools(self.guard(True, True), path)
            self.assertEqual(reconnected.invoke('uav_combination_select', target)['state'], 'selected')
            for conflict in ({'protection_profile': 'lightweight', 'auth_policy': 'SM2', 'suite': 'SM4-GCM'},
                             {'protection_profile': 'lightweight', 'auth_policy': 'RSA', 'suite': 'AES-256-GCM'}):
                with self.assertRaises(ProtocolError):
                    reconnected.invoke('uav_combination_select', conflict)
            self.assertEqual(json.loads(path.read_text()), expected)

    def test_deactivate_only_registered_deployment_and_never_expose_to_model(self):
        guard = self.guard(True, True)
        tools = GuardTools(guard)
        self.assertNotIn('uav_service_deactivate', [tool['name'] for tool in tools.tools()])
        original = guard.mcp_call
        listings = ["NAMESPACE   APIVERSION   KIND   NAME\nuav-mvp     v1           Pod    sm4-old\n", ""]
        def upstream(name, arguments):
            if name == 'resources_scale':
                self.calls.append((name, arguments))
                self.resource['spec']['replicas'] = 0
                self.resource['status'] = {'observedGeneration': 2}
                return {'content': []}
            if name == 'pods_list_in_namespace':
                self.calls.append((name, arguments))
                return {'content': [{'type': 'text', 'text': listings.pop(0)}]}
            return original(name, arguments)
        guard.mcp_call = upstream
        self.assertEqual(guard.deactivate('sm4-gcm-v1'), {'service_id': 'sm4-gcm-v1', 'stopped': True})
        self.assertEqual([call for call in self.calls if call[0] == 'resources_scale'],
                         [('resources_scale', {'apiVersion': 'apps/v1', 'kind': 'Deployment',
                                               'namespace': 'uav-mvp', 'name': 'sm4-service', 'scale': 0})])
        self.assertEqual(listings, [])
        with self.assertRaises(ProtocolError):
            guard.deactivate('unregistered')

    def test_create_absent_resources_and_reconcile_partial_creation(self):
        templates = json.loads((ROOT / 'jetson/edge_agent/config/service-templates.json').read_text())
        resources = {}
        writes = []
        fail_service = [True]
        def upstream(name, arguments):
            if name == 'resources_get':
                key = (arguments['kind'], arguments['name'])
                if key not in resources:
                    plural = {'Deployment': 'deployments.apps', 'Service': 'services', 'ConfigMap': 'configmaps'}[key[0]]
                    return {'isError': True, 'content': [{'type': 'text', 'text': 'failed to get resource: ' + plural + ' "' + key[1] + '" not found'}]}
                return {'content': [{'type': 'text', 'text': json.dumps(resources[key])}]}
            self.assertEqual(name, 'resources_create_or_update')
            item = json.loads(arguments['resource'])
            if item['kind'] == 'Service' and fail_service[0]:
                fail_service[0] = False
                return {'isError': True}
            key = (item['kind'], item['metadata']['name'])
            resources[key] = item
            writes.append(key)
            return {'content': [{'type': 'text', 'text': 'applied'}]}
        guard = self.guard(True, True)
        guard.templates, guard.mcp_call = templates, upstream
        self.assertFalse(guard.status('sm4-gcm-v1')['exists'])
        with self.assertRaises(ProtocolError):
            guard.activate('sm4-gcm-v1')
        status = guard.activate('sm4-gcm-v1')
        self.assertTrue(status['exists'])
        self.assertFalse(status['ready'])
        self.assertEqual([kind for kind, _ in writes], ['ConfigMap', 'Service', 'Deployment'])

    def test_forbidden_does_not_trigger_creation(self):
        guard = self.guard(True, True)
        guard.templates = json.loads((ROOT / 'jetson/edge_agent/config/service-templates.json').read_text())
        def upstream(name, arguments):
            self.assertEqual(name, 'resources_get')
            return {'isError': True, 'content': [{'type': 'text', 'text': 'forbidden'}]}
        guard.mcp_call = upstream
        with self.assertRaises(ProtocolError):
            guard.activate('sm4-gcm-v1')


if __name__ == '__main__':
    unittest.main()
