import concurrent.futures
import copy
import tempfile
import unittest
from pathlib import Path

from services.crypto_service.repository.crypto import CounterRepository
from shared.communication.envelope import EnvelopeCodec, ProtocolError, ReplayError, aead_encrypt


class EnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.edge = self.codec('edge')
        self.cloud = self.codec('cloud')

    def codec(self, role):
        return EnvelopeCodec(device_id='jetson-01', key_id='key-01', root_key=bytes(range(32)), role=role, counters=CounterRepository(Path(self.tmp.name)/(role+'.db')), epochs={'initial': 'AES-256-GCM', 'candidate': 'SM4-GCM'})

    def test_both_algorithms_all_business_types_bidirectional(self):
        for epoch in ('initial', 'candidate'):
            for sender, receiver in ((self.edge,self.cloud),(self.cloud,self.edge)):
                for kind in ('task.claim','task.dispatch','heartbeat','telemetry','task.result','status','switch.prepare','switch.commit'):
                    payload={'kind':kind,'text':'真实巡检遥测','position':[1.2,3.4]}
                    message=sender.seal(kind,payload,epoch=epoch)
                    self.assertNotIn('真实巡检',str(message))
                    self.assertEqual(receiver.open(message)[1],payload)

    def test_metadata_and_ciphertext_tampering_does_not_consume_sequence(self):
        for epoch in ('initial','candidate'):
            message=self.edge.seal('telemetry',{'battery':80},epoch=epoch)
            variants=[]
            for field,value in [('kind','task.dispatch'),('message_id','another'),('seq',1000),('reply_to','other')]:
                variant=copy.deepcopy(message);variant['header'][field]=value;variants.append(variant)
            variant=copy.deepcopy(message);variant['ciphertext']='AAAA';variants.append(variant)
            for variant in variants:
                with self.assertRaises(ProtocolError): self.cloud.open(variant)
            self.assertEqual(self.cloud.open(message)[1],{'battery':80})

    def test_replay_persists_across_restart(self):
        message=self.edge.seal('heartbeat',{},epoch='initial')
        self.cloud.open(message)
        with self.assertRaises(ReplayError): self.codec('cloud').open(message)
        next_message=self.codec('edge').seal('heartbeat',{},epoch='initial')
        self.assertGreater(next_message['header']['seq'],message['header']['seq'])

    def test_reordering_window_and_stale_messages(self):
        messages=[self.edge.seal('telemetry',i,epoch='initial') for i in range(70)]
        self.cloud.open(messages[-1]);self.cloud.open(messages[-3]);self.cloud.open(messages[-2])
        with self.assertRaises(ReplayError): self.cloud.open(messages[-3])
        with self.assertRaises(ReplayError): self.cloud.open(messages[0])

    def test_direction_and_session_separation(self):
        message=self.edge.seal('heartbeat',{},epoch='candidate')
        with self.assertRaises(ProtocolError): self.edge.open(message)
        self.cloud.retire_epoch('candidate')
        with self.assertRaises(ProtocolError): self.cloud.open(message)
        with self.assertRaises(ProtocolError): self.edge.allow_epoch('initial','SM4-GCM')

    def test_counter_allocation_is_atomic(self):
        def allocate(_): return self.codec('edge').seal('heartbeat',{},epoch='initial')['header']['seq']
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            values=list(pool.map(allocate,range(60)))
        self.assertEqual(sorted(values),list(range(1,61)))

    def test_aes256_gcm_known_answer(self):
        # NIST AES-256-GCM, zero key/IV/plaintext, empty AAD.
        ciphertext,tag=aead_encrypt('AES-256-GCM',bytes(32),bytes(12),bytes(16),b'')
        self.assertEqual(ciphertext.hex(),'cea7403d4d606b6e074ec5d3baf39d18')
        self.assertEqual(tag.hex(),'d0d1c8a799996bf0265b98b5d48ab919')

    def test_rfc8998_sm4_gcm_known_answer(self):
        # https://www.ietf.org/rfc/rfc8998.txt Appendix A.1.
        key=bytes.fromhex('0123456789ABCDEFFEDCBA9876543210')
        nonce=bytes.fromhex('00001234567800000000ABCD')
        plaintext=bytes.fromhex('AAAAAAAAAAAAAAAABBBBBBBBBBBBBBBBCCCCCCCCCCCCCCCCDDDDDDDDDDDDDDDDEEEEEEEEEEEEEEEEFFFFFFFFFFFFFFFFEEEEEEEEEEEEEEEEAAAAAAAAAAAAAAAA')
        aad=bytes.fromhex('FEEDFACEDEADBEEFFEEDFACEDEADBEEFABADDAD2')
        ciphertext,tag=aead_encrypt('SM4-GCM',key,nonce,plaintext,aad)
        self.assertEqual(ciphertext.hex(),'17f399f08c67d5ee19d0dc9969c4bb7d5fd46fd3756489069157b282bb200735d82710ca5c22f0ccfa7cbf93d496ac15a56834cbcf98c397b4024a2691233b8d')
        self.assertEqual(tag.hex(),'83de3541e4c2b58177e065a9bf7b62ec')


if __name__ == '__main__': unittest.main()
