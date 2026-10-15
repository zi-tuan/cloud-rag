import json
import secrets
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http_server import LiveServer
from pathlib import Path

from services.crypto_service.service.crypto import CryptoService
from services.crypto_service.controller.crypto import create_app
from shared.communication.envelope import ProtocolError, ReplayError
from sqlalchemy import event


class CryptoServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'key').write_bytes(secrets.token_bytes(32))
        self.token=secrets.token_hex(32);(self.root/'token').write_text(self.token)

    def app(self,role,suite,initialize=True,broken=False):
        return CryptoService(dict(suite=suite,device_id='jetson-01',key_id='key-01',role=role,token_file=str(self.root/'token'),key_file=str(self.root/'key'),state_path=str(self.root/(role+suite+'.db')),initialize=initialize,initial_epoch='baseline' if suite=='AES-256-GCM' else None,broken=broken))

    def server(self,app):
        server=LiveServer(create_app(app, self.token))
        server.start();thread=server.thread
        def stop(): server.shutdown();server.server_close();thread.join()
        self.addCleanup(stop)
        return 'http://127.0.0.1:'+str(server.server_port)

    def request(self,url,path,data=None,token=None):
        req=urllib.request.Request(url+path,data=None if data is None else json.dumps(data).encode(),headers={'Authorization':'Bearer '+(token if token is not None else self.token),'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=5) as res: return json.load(res)

    def test_http_actual_aes_and_sm4_roundtrip_with_authentication(self):
        for suite in ('AES-256-GCM','SM4-GCM'):
            edge=self.server(self.app('edge',suite));cloud=self.server(self.app('cloud',suite))
            for url in (edge,cloud):
                self.request(url,'/v1/sessions/lease',{'epoch':'test-epoch','protection_profile':'lightweight','expires_at':int(time.time())+900})
                self.request(url,'/v1/sessions/prepare',{'epoch':'test-epoch'})
            payload={'latitude':31.2,'longitude':121.4,'task':'switch to SM4'}
            encrypted=self.request(edge,'/v1/seal',{'kind':'telemetry','payload':payload,'epoch':'test-epoch'})
            self.assertEqual(self.request(cloud,'/v1/open',{'envelope':encrypted})['payload'],payload)
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(edge,'/v1/seal',{'kind':'heartbeat','payload':{},'epoch':'test-epoch'},token='wrong')
            self.assertEqual(error.exception.code,401)

    def test_session_preparation_survives_restart(self):
        first=self.app('edge','SM4-GCM');first.lease('candidate','lightweight',int(time.time())+900);first.prepare('candidate')
        message=first.seal('heartbeat', {}, epoch='candidate')
        restarted=self.app('edge','SM4-GCM',initialize=False)
        next_message=restarted.seal('heartbeat', {}, epoch='candidate')
        self.assertGreater(next_message['header']['seq'],message['header']['seq'])

    def test_missing_state_fails_closed_and_broken_version_not_ready(self):
        with self.assertRaises(ValueError): self.app('edge','SM4-GCM',initialize=False)
        url=self.server(self.app('edge','SM4-GCM',broken=True))
        with self.assertRaises(urllib.error.HTTPError) as error: self.request(url,'/ready')
        self.assertEqual(error.exception.code,503)
        self.assertEqual(self.request(url,'/health')['suite'],'SM4-GCM')

    def test_retirement_survives_restart_and_cannot_resurrect_key(self):
        edge = self.app('edge', 'SM4-GCM')
        cloud = self.app('cloud', 'SM4-GCM')
        deadline = int(time.time()) + 900
        for service in (edge, cloud):
            service.lease('candidate','lightweight',deadline)
            service.prepare('candidate')
        message = edge.seal('heartbeat', {}, epoch='candidate')
        cloud.open(message)
        for service in (edge, cloud): service.retire('candidate')
        edge = self.app('edge', 'SM4-GCM', initialize=False)
        cloud = self.app('cloud', 'SM4-GCM', initialize=False)
        with self.assertRaises(ProtocolError): edge.seal('heartbeat', {}, epoch='candidate')
        for service in (edge, cloud):
            with self.assertRaises(ProtocolError): service.prepare('candidate')
            with self.assertRaises(ProtocolError): service.lease('candidate','lightweight',deadline)
        with self.assertRaises(ProtocolError): cloud.open(message)

    def test_key_lifetime_cannot_extend_and_expiry_survives_restart(self):
        now = int(time.time())
        service = self.app('edge','SM4-GCM')
        service.lease('short','enhanced',now+120)
        service.prepare('short')
        with self.assertRaises(ProtocolError): service.lease('short','lightweight',now+900)
        service.clock = lambda: now + 120
        with self.assertRaises(ProtocolError): service.seal('heartbeat',{},epoch='short')
        restarted = self.app('edge','SM4-GCM',initialize=False)
        restarted.clock = service.clock
        with self.assertRaises(ProtocolError): restarted.prepare('short')

    def test_failed_prepare_commit_does_not_activate_session(self):
        service = self.app('edge', 'SM4-GCM')
        service.lease('candidate','lightweight',int(time.time())+900)

        def fail_commit(connection): raise RuntimeError('commit failed')

        event.listen(service.repository.engine, 'commit', fail_commit)
        with self.assertRaises(RuntimeError): service.prepare('candidate')
        event.remove(service.repository.engine, 'commit', fail_commit)
        self.assertEqual(service.repository.epochs(service.suite), {})
        with self.assertRaises(ProtocolError): service.seal('heartbeat', {}, epoch='candidate')

    def test_invalid_and_conflicting_epoch_do_not_activate_session(self):
        service = self.app('edge', 'SM4-GCM')
        for epoch in ('', 'bad epoch', 'x' * 129):
            with self.assertRaises(ProtocolError): service.prepare(epoch)
        self.assertEqual(service.repository.epochs(service.suite), {})
        service.repository.prepare_epoch('conflict', 'AES-256-GCM')
        service.lease('conflict','lightweight',int(time.time())+900)
        with self.assertRaises(ProtocolError): service.prepare('conflict')
        with self.assertRaises(ProtocolError): service.seal('heartbeat', {}, epoch='conflict')

    def test_short_service_token_is_rejected_by_controller(self):
        with self.assertRaises(ValueError): create_app(self.app('edge', 'SM4-GCM'), 'short')


if __name__=='__main__': unittest.main()
