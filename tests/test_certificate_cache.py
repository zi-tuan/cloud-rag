import copy
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from shared.communication import auth_certificates
from shared.communication.auth_providers import provider_from_config
from shared.communication.envelope import ProtocolError
from deploy.scripts.provision_certificate_identity import provision


class CertificateCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        binary = os.environ.get('UAV_TEST_OPENSSL_BINARY') or shutil.which('openssl')
        configs = {}
        for role in ('edge', 'cloud'):
            directory = cls.root / role
            config = provision(directory, cls.root / ('issuer-' + role), role, 'SM2', 'jetson', 'control', binary=binary)
            for name in list(config):
                if name.endswith('_file'):
                    config[name] = str(directory / Path(config[name]).name)
            config['openssl_binary'] = binary
            configs[role] = config
        directory, other = cls.root / 'edge', cls.root / 'cloud'
        for source, destination in (('public.pem', 'peer.pem'), ('own-ca.pem', 'peer-ca.pem'), ('own-crl.pem', 'peer-crl.pem')):
            shutil.copyfile(other / source, directory / destination)
        cls.config = configs['edge']

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.now = int(time.time()) + 5
        self.provider = provider_from_config(copy.deepcopy(self.config), clock=lambda: self.now)

    def test_reuses_verified_material_and_rechecks_clock_rollback(self):
        with patch('shared.communication.auth_certificates.openssl', wraps=auth_certificates.openssl) as verify:
            for _ in range(3):
                self.provider.trust.check()
            self.now += 10
            self.provider.trust.check()
            self.assertEqual(verify.call_count, 0)
            self.now -= 11
            self.provider.trust.check()
            self.assertEqual(verify.call_count, 2)

    def test_rejects_changed_crl_even_with_unchanged_mtime(self):
        path = Path(self.provider.config['peer_crl_file'])
        original, stat = path.read_bytes(), path.stat()
        try:
            path.write_bytes(Path(self.provider.config['own_crl_file']).read_bytes())
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            with self.assertRaises(ProtocolError):
                self.provider.trust.check()
        finally:
            path.write_bytes(original)

    def test_rejects_expired_crl_and_replaced_identity(self):
        crl = x509.load_pem_x509_crl(Path(self.provider.config['peer_crl_file']).read_bytes())
        self.now = int(crl.next_update_utc.timestamp()) + 1
        with self.assertRaises(ProtocolError):
            self.provider.trust.check()
        self.now = int(time.time())
        path = Path(self.provider.config['peer_certificate_file'])
        original = path.read_bytes()
        try:
            path.write_bytes(Path(self.provider.config['own_certificate_file']).read_bytes())
            with self.assertRaises(ProtocolError):
                self.provider.trust.check()
        finally:
            path.write_bytes(original)

    def test_valid_updated_crl_rejects_revoked_peer(self):
        path = Path(self.provider.config['peer_crl_file'])
        original = path.read_bytes()
        binary = self.provider.config['openssl_binary']
        issuer = self.root / 'issuer-cloud' / 'issuer.cnf'
        try:
            subprocess.run([binary, 'ca', '-batch', '-config', str(issuer), '-revoke', str(self.root / 'cloud' / 'public.pem')],
                           check=True, capture_output=True, timeout=10)
            subprocess.run([binary, 'ca', '-gencrl', '-config', str(issuer), '-out', str(path)],
                           check=True, capture_output=True, timeout=10)
            with self.assertRaises(ProtocolError):
                self.provider.trust.check()
        finally:
            path.write_bytes(original)
