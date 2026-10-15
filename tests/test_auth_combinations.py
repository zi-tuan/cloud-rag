import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from shared.communication.auth_client import PolicyProviders
from shared.communication.auth_providers import provider_from_config
from shared.communication.envelope import ProtocolError
from shared.communication.targets import explicit_combination
from deploy.scripts.provision_certificate_identity import provision
import test_auth


def fixture(root):
    binary = os.environ.get('UAV_TEST_OPENSSL_BINARY') or shutil.which('openssl')
    if not binary:
        raise RuntimeError('OpenSSL is required for RSA/SM2 interoperability tests')
    configs = {'edge': {}, 'cloud': {}}
    for policy in ('RSA', 'SM2'):
        for role in configs:
            directory = root / (role + '-' + policy)
            config = provision(directory, root / ('issuer-' + role + '-' + policy), role, policy, 'jetson', 'control', binary=binary)
            for name in list(config):
                if name.endswith('_file'):
                    config[name] = str(directory / Path(config[name]).name)
            config['openssl_binary'] = binary
            configs[role][policy] = config
        for role, peer in (('edge', 'cloud'), ('cloud', 'edge')):
            directory, other = root / (role + '-' + policy), root / (peer + '-' + policy)
            for source, destination in (('public.pem', 'peer.pem'), ('own-ca.pem', 'peer-ca.pem'), ('own-crl.pem', 'peer-crl.pem')):
                shutil.copyfile(other / source, directory / destination)
    return configs


class CombinationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.configs = fixture(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        # 共用现有真实协议测试的传输夹具，不继承其测试方法。
        test_auth.AuthTests.setUp(self)
        self.now = int(time.time())
        routers = {}
        for role in ('edge', 'cloud'):
            self.providers[role].config['not_after'] = self.now + 86400
            providers = {'Ed25519': self.providers[role]}
            providers.update({policy: provider_from_config(copy.deepcopy(config), clock=lambda: self.now)
                              for policy, config in self.configs[role].items()})
            routers[role] = PolicyProviders(providers)
        self.routers = routers
        self.cloud.provider = self.app.codec.provider = routers['cloud']
        self.edge.provider = self.client.codec.provider = routers['edge']
        self.cloud.policy_for_epoch = self.states['cloud'].policy
        self.edge.policy_for_epoch = self.states['edge'].policy
        self.app.switch.auth_ready = routers['cloud'].ready
        self.coordinator.auth_ready = routers['edge'].ready

    def test_six_combinations_and_authentication_only_changes(self):
        self.client.exchange('heartbeat', {})
        for index, (policy, suite) in enumerate((p, s) for p in ('RSA', 'SM2', 'Ed25519') for s in ('AES-256-GCM', 'SM4-GCM')):
            with self.subTest(policy=policy, suite=suite):
                self.now += 125
                result = self.coordinator.start('combo-' + str(index), 'combo-epoch-' + str(index), suite, auth_policy=policy)
                self.assertEqual(result['state'], 'succeeded')
                self.assertTrue(self.client.exchange('heartbeat', {})['accepted'])
                actual = self.cloud.status(self.states['cloud'].active())
                self.assertEqual((actual['policy'], actual['suite']), (policy, suite))
                self.assertEqual(self.states['edge'].policy(self.states['edge'].active()), policy)
        # 同一种通信加密，仅迁移身份认证也必须产生新 epoch。
        old = self.states['edge'].active()
        self.now += 125
        self.assertEqual(self.coordinator.start('auth-only', 'auth-only-epoch', 'SM4-GCM', auth_policy='RSA')['state'], 'succeeded')
        self.assertNotEqual(old, self.states['edge'].active())
        self.assertEqual(self.cloud.status('auth-only-epoch')['policy'], 'RSA')

    def test_policy_conflict_and_no_fallback_when_target_unavailable(self):
        self.client.exchange('heartbeat', {})
        with self.assertRaises(ProtocolError):
            self.coordinator.start('bad', 'bad-epoch', 'AES-256-GCM', auth_policy='unknown')
        self.routers['edge'].providers['RSA'].config['revoked'] = True
        with self.assertRaises(ProtocolError):
            self.coordinator.start('revoked', 'revoked-epoch', 'AES-256-GCM', auth_policy='RSA')
        self.assertEqual(self.states['edge'].active(), 'aes0')
        binding = {'protection_profile': 'lightweight', 'device_id': 'jetson', 'request_id': 'binding', 'old_epoch': 'aes0',
                   'epoch': 'candidate', 'suite': 'SM4-GCM', 'auth_policy': 'SM2'}
        self.states['cloud'].begin(binding)
        body = {'device_id': 'jetson', 'key_id': 'key', 'epoch': 'candidate', 'suite': 'SM4-GCM',
                'policy': 'RSA', 'credential_version': '1', 'client_nonce': 'a' * 64, 'request_id': 'wrong-policy'}
        with self.assertRaises(ProtocolError):
            self.cloud.start(body)

    def test_wrong_sm2_identifier_and_certificate_trust_rejected(self):
        config = copy.deepcopy(self.configs['edge']['SM2'])
        config['sm2_id'] = 'wrong-id'
        with self.assertRaises(ProtocolError):
            provider_from_config(config, clock=lambda: self.now)
        config = copy.deepcopy(self.configs['edge']['RSA'])
        config['peer_ca_file'] = config['own_ca_file']
        with self.assertRaises(ProtocolError):
            provider_from_config(config, clock=lambda: self.now)
        config = copy.deepcopy(self.configs['edge']['RSA'])
        config['cloud_id'] = 'unregistered'
        with self.assertRaises(ProtocolError):
            provider_from_config(config, clock=lambda: self.now)

    def test_natural_language_combinations_and_ambiguous_input(self):
        for text, policy, suite in (
                ('切换到 SM2 + SM4', 'SM2', 'SM4-GCM'),
                ('请将身份认证切换到 RSA，通信保持 AES-256-GCM。', 'RSA', 'AES-256-GCM'),
                ('将身份认证切换到 SM2，保持当前加密算法不变。', 'SM2', None),
                ('切换到 Ed25519 + AES', 'Ed25519', 'AES-256-GCM')):
            self.assertEqual(explicit_combination(text), {'auth_policy': policy, 'suite': suite})
        self.assertIsNone(explicit_combination('比较 RSA 或 SM2，选择一个合适方案'))
        self.assertIsNone(explicit_combination('不要切换到 SM2'))


if __name__ == '__main__':
    unittest.main()
