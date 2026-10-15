import copy
import json
import unittest

from shared.communication.envelope import ProtocolError
from deploy.scripts.render_auth_manifests import manifests
from jetson.edge_agent.auth_lifecycle import AuthenticationCatalog, AuthenticationOrchestrator


class AuthenticationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.resources, entries = {}, {}
        self.calls = []
        for policy in ('Ed25519', 'RSA', 'SM2'):
            resource = manifests('uav-auth-ed25519:arm64-verified', {'policy': policy})['items'][2]
            resource['metadata']['generation'] = 1
            resource['status'] = {'observedGeneration': 1}
            pod = resource['spec']['template']['spec']
            container = pod['containers'][0]
            self.resources['auth-' + policy.lower()] = resource
            entries[policy] = {'namespace': 'uav-mvp', 'deployment': 'auth-' + policy.lower(),
                'container_name': 'auth', 'image': container['image'], 'resources': container['resources'],
                'health_path': None, 'registry_verified': True, 'pod_spec': copy.deepcopy(pod)}
        self.guard = AuthenticationOrchestrator(AuthenticationCatalog(entries), self.upstream, writes_enabled=True)

    def upstream(self, name, arguments):
        self.calls.append((name, arguments))
        if name == 'pods_list_in_namespace':
            return {'content': [{'type': 'text', 'text': ''}]}
        resource = self.resources[arguments['name']]
        if name == 'resources_scale':
            count = arguments['scale']
            resource['spec']['replicas'] = count
            resource['metadata']['generation'] += 1
            resource['status'] = {'observedGeneration': resource['metadata']['generation'],
                **{key: count for key in ('replicas', 'readyReplicas', 'updatedReplicas', 'availableReplicas')}}
        return {'content': [{'type': 'text', 'text': json.dumps(resource)}]}

    def test_candidate_starts_without_stopping_current_then_retain_stops_others(self):
        self.guard.ensure('SM2')
        self.guard.ensure('RSA')
        self.assertEqual(self.resources['auth-sm2']['spec']['replicas'], 1)
        self.assertEqual(self.resources['auth-rsa']['spec']['replicas'], 1)
        self.assertEqual(self.guard.retain('RSA'), {'auth_policy': 'RSA', 'exclusive': True})
        self.assertEqual({name: item['spec']['replicas'] for name, item in self.resources.items()},
                         {'auth-ed25519': 0, 'auth-rsa': 1, 'auth-sm2': 0})
        self.assertTrue(any(name == 'pods_list_in_namespace' for name, _ in self.calls))

    def test_unknown_policy_and_isolation_drift_do_not_scale(self):
        with self.assertRaises(ProtocolError):
            self.guard.ensure('unknown')
        self.resources['auth-rsa']['spec']['template']['spec']['volumes'].append(
            {'name': 'root', 'hostPath': {'path': '/root.key'}})
        with self.assertRaises(ProtocolError):
            self.guard.ensure('RSA')
        self.assertFalse(any(name == 'resources_scale' for name, _ in self.calls))

    def test_disabled_writes_and_missing_identity_fail_closed(self):
        self.guard.writes_enabled = False
        with self.assertRaises(ProtocolError):
            self.guard.ensure('SM2')
        self.guard.writes_enabled = True
        self.guard.deployment = lambda entry: None
        with self.assertRaises(ProtocolError):
            self.guard.ensure('SM2')
        self.assertFalse(any(name == 'resources_create_or_update' for name, _ in self.calls))
