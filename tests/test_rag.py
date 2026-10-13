import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from uav_rag.index import build, KnowledgeBase
from uav_rag.server import make_server, Artifacts
from uav_rag.client import RagClient
from uav_rag.workflow import run_workflow
from uav_rag.pi_runner import runner_class
from uav_rag.catalog import SERVICES
from uav_rag.descriptions import DESCRIPTIONS
from uav_rag.retrieval import encoded, query_page
from uav_rag.tools import RagTools
from uav_rag.mcp_guard import guard_class

ROOT = Path(__file__).resolve().parents[1]


class RagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.tmp.name)
        cls.source = ROOT/'sources/GmSSL' if (ROOT/'sources/GmSSL').exists() else ROOT.parent/'GmSSL'
        cls.db = cls.directory/'gmssl.sqlite3'
        cls.stats = build(cls.source,cls.db)
        cls.kb = KnowledgeBase(cls.db)
        cls.token = 'test-token-'+'a'*40
        # Distribution fixture tests bytes only, not a usable service image.
        payload = b'OCI archive transport fixture\n'*100
        (cls.directory/'sample.tar').write_bytes(payload)
        cls.artifact_digest = hashlib.sha256(payload).hexdigest()
        cls.manifest = cls.directory/'artifacts.json'
        cls.manifest.write_text(json.dumps({'services':{'sm4-gcm-v1':dict(file='sample.tar',
            sha256=cls.artifact_digest,architecture='arm64',format='oci-archive')}}))
        cls.server = make_server(cls.kb,port=0,token=cls.token,artifacts=Artifacts(cls.manifest))
        cls.worker = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.worker.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.client = RagClient(cls.url,cls.token)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()
        cls.tmp.cleanup()

    def test_corpus_and_citations_match_source(self):
        self.assertGreater(self.stats['documents'],100)
        result = self.client.enrich('test-1','SM4 GCM 加密完整性',['SM4-GCM'])
        service = result['services'][0]
        self.assertEqual(service['evidence_status'],'available')
        self.assertEqual(service['service_id'],'sm4-gcm-v1')
        for citation in service['citations']:
            raw = (self.source/citation['path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),citation['sha256'])
            lines = raw.decode('utf-8-sig').splitlines()
            self.assertEqual('\n'.join(lines[citation['start_line']-1:citation['end_line']]),citation['text'])

    def test_retrieval_ranking(self):
        results = self.kb.search('sm4 gcm encrypt',limit=5)
        self.assertTrue(any('sm4_gcm' in r['path'] or r['path']=='include/gmssl/sm4.h' for r in results))
        results = self.kb.search('证书 撤销 x509 crl verify',limit=5)
        self.assertTrue(any('crl' in r['path'] or 'x509' in r['path'] for r in results))

    def test_all_evidence_classes(self):
        services = self.client.enrich('coverage','签名和加密',['SM2','AES-256-GCM','RSA','Ed25519','unknown-service'])['services']
        self.assertEqual([s['evidence_status'] for s in services],['available','available','insufficient','not_found','not_found'])
        self.assertFalse(services[0]['artifact']['available'])
        self.assertEqual(services[-1]['citations'],[])

    def test_chinese_query(self):
        result = self.client.enrich('cn','需要消息摘要与密钥派生',['SM3','HKDF'])
        self.assertTrue(all(s['citations'] for s in result['services']))

    def test_algorithm_descriptions_persisted_and_returned(self):
        with closing(self.kb.connect()) as db:
            rows = db.execute('SELECT * FROM algorithm_descriptions').fetchall()
        self.assertEqual({row['service_id'] for row in rows}, set(SERVICES))
        self.assertEqual(self.stats['description_count'], len(SERVICES))
        result = self.client.enrich('descriptions', '用途、优点、缺点与限制', list(SERVICES))
        for service in result['services']:
            description = service['description']
            self.assertEqual(description, DESCRIPTIONS[service['service_id']])
            self.assertTrue(description['purpose'])
            for field in ('advantages', 'limitations', 'sources'):
                self.assertGreater(len(description[field]), 0)
        by_id = {s['service_id']: s for s in result['services']}
        self.assertEqual(by_id['auth-rsa']['evidence_status'], 'insufficient')
        self.assertEqual(by_id['auth-ed25519']['evidence_status'], 'not_found')
        self.assertEqual(by_id['auth-ed25519']['citations'], [])

    def test_descriptions_read_from_database_not_runtime_catalog(self):
        original = DESCRIPTIONS['sm4-gcm-v1']['purpose']
        try:
            DESCRIPTIONS['sm4-gcm-v1']['purpose'] = 'not indexed'
            result = self.kb.enrich(dict(task_id='stored', query='用途', candidate_services=['SM4-GCM']))
            self.assertEqual(result['services'][0]['description']['purpose'], original)
        finally:
            DESCRIPTIONS['sm4-gcm-v1']['purpose'] = original

    def test_unknown_is_not_filled_with_unrelated_results(self):
        item = self.client.enrich('unknown','SM4 加密',['unregistered-foo'])['services'][0]
        self.assertEqual(item['evidence_status'],'not_found')
        self.assertEqual(item['citations'],[])

    def test_authentication(self):
        for token in (None,'wrong'):
            with self.assertRaises(HTTPError) as error:
                RagClient(self.url,token).enrich('test','sm4',['SM4-GCM'])
            self.assertEqual(error.exception.code,401)

    def test_validation(self):
        valid = dict(task_id='t',query='sm4',candidate_services=['SM4-GCM'])
        for change in ({'top_k':True},{'top_k':6},{'query':''},{'candidate_services':[]},
                       {'candidate_services':['../secret']},{'candidate_services':'sm4'}, {'extra':1}):
            with self.subTest(change=change), self.assertRaises(HTTPError) as error:
                self.client.request('/v1/services/enrich',{**valid,**change})
            self.assertEqual(error.exception.code,400)

    def test_bounded_request(self):
        with self.assertRaises(HTTPError) as error:
            self.client.request('/v1/services/enrich',{'x':'a'*65537})
        self.assertEqual(error.exception.code,413)

    def test_no_path_traversal(self):
        with self.assertRaises(HTTPError) as error:
            self.client.request('/v1/artifacts/../../README.md')
        self.assertEqual(error.exception.code,404)

    def test_remote_http_and_unprotected_bind_rejected(self):
        with self.assertRaises(ValueError):
            RagClient('http://example.org:8787')
        with self.assertRaises(ValueError):
            make_server(self.kb,'0.0.0.0',0)

    def test_download_checksum_and_cache(self):
        service = self.client.enrich('download','sm4',['SM4-GCM'])['services'][0]
        path = self.client.download(service,self.directory/'cache')
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),self.artifact_digest)
        self.assertEqual(path,self.client.download(service,self.directory/'cache'))
        bad = {**service,'artifact':{**service['artifact'],'sha256':'0'*64}}
        with self.assertRaises(ValueError):
            self.client.download(bad,self.directory/'bad-cache')
        self.assertEqual(list((self.directory/'bad-cache').iterdir()),[])
        with self.assertRaises(ValueError):
            self.client.download(service,self.directory/'cache',architecture='amd64')

    def test_download_missing_artifact(self):
        service = self.client.enrich('missing','sm2',['SM2'])['services'][0]
        with self.assertRaises(ValueError):
            self.client.download(service,self.directory/'missing-cache')

    def test_manifest_escape_rejected(self):
        manifest = self.directory/'escape.json'
        manifest.write_text(json.dumps({'services':{'sm4-gcm-v1':dict(file='../escape.tar',sha256='0'*64,architecture='arm64',format='oci-archive')}}))
        with self.assertRaises(ValueError):
            Artifacts(manifest)

    def test_mutated_artifact_rejected(self):
        path = self.directory/'sample.tar'
        raw = path.read_bytes()
        try:
            path.write_bytes(b'changed')
            with self.assertRaises(HTTPError) as error:
                self.client.request('/v1/artifacts/sm4-gcm-v1')
            self.assertEqual(error.exception.code,409)
        finally:
            path.write_bytes(raw)

    def test_tool_workflow_long_task_real_http(self):
        scenario = '任务要求' * 4000
        def agent(prompt, tools):
            self.assertEqual(prompt, scenario)
            arguments = dict(query='SM4 加密接口', candidate_services=['sm4-gcm-v1'])
            while True:
                page = tools.invoke('uav_knowledge_query', arguments)
                found = [item['id'] for item in page['items'] if item['kind'] == 'citation']
                if found:
                    return dict(selected_services=['sm4-gcm-v1'], reason='有来源证据', citation_ids=[found[0]])
                self.assertIsNotNone(page['next_cursor'])
                arguments['cursor'] = page['next_cursor']
        result = run_workflow(dict(task_id='pipeline', scenario=scenario), agent, self.client)
        self.assertEqual(result['state'], 'planned')
        self.assertTrue(result['requires_execution_verification'])

    def test_fabricated_selection_rejected(self):
        with self.assertRaises(ValueError):
            run_workflow(dict(task_id='fake', scenario='sm4'),
                lambda prompt, tools: dict(selected_services=['auth-sm2'], reason='伪造', citation_ids=['fake']), self.client)

    def test_abstention(self):
        result = run_workflow(dict(task_id='none', scenario='Ed25519'),
            lambda prompt, tools: dict(selected_services=[], reason='资料不足', citation_ids=[]), self.client)
        self.assertEqual(result['state'], 'insufficient_evidence')

    def test_disconnect_prevents_evidence_selection(self):
        class Offline:
            def query(self, *args, **kwargs):
                raise OSError('offline')
        def agent(prompt, tools):
            return tools.invoke('uav_knowledge_query', dict(query='sm4', candidate_services=['SM4-GCM']))
        with self.assertRaises(OSError):
            run_workflow(dict(task_id='offline', scenario='sm4'), agent, Offline())

    def test_index_rebuild_is_reproducible(self):
        other = self.directory/'other.sqlite3'
        self.assertEqual(build(self.source,other)['revision'],self.stats['revision'])
        kb = KnowledgeBase(other)
        self.assertEqual(kb.search('sm4 gcm'),self.kb.search('sm4 gcm'))

    def test_read_only_index(self):
        with closing(self.kb.connect()) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM chunks')

    def test_all_candidates_pagination_is_bounded_and_lossless(self):
        candidates = list(SERVICES) * 2  # 16 inputs, eight unique algorithms.
        args = dict(query='签名 加密 优缺点', candidates=candidates, max_bytes=4096)
        items, seen = [], set()
        while True:
            page = self.client.query(**args)
            self.assertLessEqual(len(encoded(page)), 4096)
            items.extend(page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                break
            self.assertNotIn(cursor, seen)
            seen.add(cursor)
            self.assertLess(len(seen), 200)
            args['cursor'] = cursor
        self.assertGreater(len(seen), 1)
        self.assertEqual([i['service_id'] for i in items if i['kind'] == 'service'], list(SERVICES))
        original = self.client.enrich('compare', args['query'], candidates, top_k=1)
        for service in original['services']:
            for field, values in service['description'].items():
                expected = values if isinstance(values, list) else [values]
                actual = [i['value'] for i in items if i['service_id'] == service['service_id']
                          and i['kind'] == 'description' and i['field'] == field]
                self.assertEqual(actual, expected)
            for citation in service['citations']:
                parts = [i for i in items if i['kind'] == 'citation' and i['id'] == citation['id']]
                self.assertEqual(''.join(p['text'] for p in parts), citation['text'])
                for part in parts:
                    self.assertEqual(part['text'], citation['text'][part['text_offset']:part['text_offset']+len(part['text'])])
                    for key in ('path', 'sha256', 'start_line', 'end_line'):
                        self.assertEqual(part[key], citation[key])

    def test_knowledge_query_validation_and_cursor_binding(self):
        args = dict(query='加密', candidate_services=list(SERVICES))
        page = query_page(self.kb, args)
        self.assertIsNotNone(page['next_cursor'])
        for change in ({'query': '中'*513}, {'query': ' '*512+'中'}, {'query': ''}, {'max_bytes': True},
                       {'max_bytes': 4095}, {'max_bytes': 12001}, {'cursor': 'bad'},
                       {'cursor': 'W10='}, {'top_k': True}, {'candidate_services': []},
                       {'extra': 1}, {'query': 'changed', 'cursor': page['next_cursor']}):
            with self.subTest(change=change), self.assertRaises(HTTPError) as error:
                self.client.request('/v1/knowledge/query', {**args, **change})
            self.assertEqual(error.exception.code, 400)
        revision = self.kb.metadata['description_revision']
        try:
            self.kb.metadata['description_revision'] = 'new-description'
            with self.assertRaises(ValueError):
                query_page(self.kb, {**args, 'cursor': page['next_cursor']})
        finally:
            self.kb.metadata['description_revision'] = revision
        with self.assertRaises(HTTPError) as error:
            RagClient(self.url).query('sm4', ['SM4-GCM'])
        self.assertEqual(error.exception.code, 401)

    def test_tool_total_budget_and_failed_attempts(self):
        tools = RagTools(self.client)
        while tools.calls < tools.max_calls and tools.remaining >= 4096:
            tools.invoke('uav_knowledge_query', dict(query='加密', candidate_services=list(SERVICES)))
        self.assertLessEqual(sum(len(encoded(p)) for p in tools.pages), 24000)
        with self.assertRaises(ValueError):
            tools.invoke('uav_knowledge_query', dict(query='加密', candidate_services=list(SERVICES)))
        class Offline:
            def query(self, *args, **kwargs):
                raise OSError('offline')
        tools = RagTools(Offline(), max_calls=2)
        for _ in range(2):
            with self.assertRaises(OSError):
                tools.invoke('uav_knowledge_query', dict(query='sm4', candidate_services=['SM4-GCM']))
        with self.assertRaises(ValueError):
            tools.invoke('uav_knowledge_query', dict(query='sm4', candidate_services=['SM4-GCM']))

    def test_guard_requires_lookup_and_preserves_original_dispatch(self):
        class Base:
            def tools(self):
                return [{'name': 'original'}]
            def invoke(self, name, arguments):
                return (name, arguments)
        guard = guard_class(Base)(rag_client=self.client)
        self.assertEqual([t['name'] for t in guard.tools()], ['original', 'uav_knowledge_query'])
        target = dict(auth_policy='Ed25519', suite=None, protection_profile='lightweight')
        with self.assertRaises(ValueError):
            guard.invoke('uav_combination_select', target)
        # Missing source evidence does not mean a registered service is absent.
        guard.invoke('uav_knowledge_query', dict(query='签名用途', candidate_services=['auth-ed25519']))
        self.assertEqual(guard.invoke('uav_combination_select', target), ('uav_combination_select', target))
        self.assertEqual(guard.invoke('uav_service_status', {'service_id': 'sm4-gcm-v1'}),
                         ('uav_service_status', {'service_id': 'sm4-gcm-v1'}))
        with self.assertRaises(ValueError):
            guard.invoke('uav_protection_plan', {'phases': [{'target': {'auth_policy': 'SM2', 'suite': 'SM4-GCM'}}]})

    def test_tool_rejects_revision_change(self):
        from unittest.mock import patch
        tools = RagTools(self.client)
        args = dict(query='用途', candidate_services=['auth-ed25519'])
        page = tools.invoke('uav_knowledge_query', args)
        with patch.object(self.client, 'query', return_value={**page, 'description_revision': 'changed'}):
            with self.assertRaises(ValueError):
                tools.invoke('uav_knowledge_query', args)
        self.assertEqual(len(tools.pages), 1)

    def test_stdio_mcp_real_http(self):
        token_path = self.directory / 'mcp-client.token'
        token_path.write_text(self.token)
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'uav_knowledge_query',
                'arguments': {'query': '国密加密', 'candidate_services': ['sm4-gcm-v1']}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'uav_knowledge_query',
                'arguments': {'query': '长'*16000, 'candidate_services': ['sm4-gcm-v1']}}},
        ]
        result = subprocess.run([sys.executable, '-m', 'uav_rag.mcp'],
            input=''.join(json.dumps(m, ensure_ascii=False)+'\n' for m in messages),
            text=True, encoding='utf-8', capture_output=True, check=True, timeout=20, cwd=ROOT,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'UAV_RAG_URL': self.url,
                 'UAV_RAG_TOKEN_FILE': str(token_path)})
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([m['id'] for m in replies], [1, 2, 3, 4])
        self.assertEqual(replies[1]['result']['tools'][0]['name'], 'uav_knowledge_query')
        text = replies[2]['result']['content'][0]['text']
        self.assertLessEqual(len(text.encode('utf-8')), 6000)
        self.assertTrue(json.loads(text)['items'])
        self.assertTrue(replies[3]['result']['isError'])
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_original_guard_with_retrieval(self):
        source = os.environ.get('UAV_PROJECT_ROOT')
        if not source:
            self.skipTest('set UAV_PROJECT_ROOT for original guard contract test')
        sys.path.insert(0, source)
        try:
            from jetson.edge_agent.mcp_guard import GuardTools
            guard = guard_class(GuardTools)(None, rag_client=self.client)
            names = {t['name'] for t in guard.tools()}
            self.assertIn('uav_knowledge_query', names)
            self.assertIn('uav_protection_plan', names)
            self.assertEqual(guard.invoke('uav_authentication_policies', {})['policies'], ['Ed25519', 'RSA', 'SM2'])
            with self.assertRaises(ValueError):
                guard.invoke('uav_combination_select', dict(auth_policy='SM2', suite='SM4-GCM'))
            page = guard.invoke('uav_knowledge_query', dict(query='SM2 签名', candidate_services=['auth-sm2']))
            self.assertEqual(page['items'][0]['service_id'], 'auth-sm2')
            with self.assertRaises(ValueError):
                guard.invoke('arbitrary_shell', {'command': 'anything'})
        finally:
            sys.path.remove(source)

    def test_original_pi_runner_overlay(self):
        # Optional integration against the actual UAV source snapshot, no edits to it.
        source = os.environ.get('UAV_PROJECT_ROOT')
        if not source:
            self.skipTest('set UAV_PROJECT_ROOT for original PiRunner contract test')
        sys.path.insert(0,source)
        from jetson.pi_runner.app import PiRunner
        from jetson.edge_agent.executor import ExecutionJournal
        journal = self.directory/'runner.db'
        ExecutionJournal(journal,initialize=True)
        receipts = self.directory/'receipts'
        receipts.mkdir(exist_ok=True)
        script = self.directory/'agent_fixture.py'
        script.write_text('''import json,os,sys
from pathlib import Path
from uav_rag.tools import environment_client, RagTools
prompt=sys.stdin.read()
assert '--no-tools' not in sys.argv
assert 'RAG 检索资料' not in prompt and 'sha256' not in prompt
assert '边界任务' * 4000 in prompt
tools=RagTools(environment_client())
page=tools.invoke('uav_knowledge_query', {'query':'SM4 加密', 'candidate_services':['sm4-gcm-v1']})
assert page['items']
Path(os.environ['UAV_SELECTION_FILE']).write_text(json.dumps({'selection_kind':'combination','target':{'auth_policy':'SM2','suite':'SM4-GCM','protection_profile':'lightweight'}}))
print('fixture MCP receipt; requires executor verification')
''',encoding='utf-8')
        token_path = self.directory/'client.token'
        token_path.write_text(self.token)
        previous = {k:os.environ.get(k) for k in ('UAV_RAG_URL','UAV_RAG_TOKEN_FILE')}
        runner = None
        try:
            os.environ.update(UAV_RAG_URL=self.url,UAV_RAG_TOKEN_FILE=str(token_path))
            runner = runner_class(PiRunner)(dict(journal=str(journal),receipts=str(receipts),
                command=[sys.executable,str(script)],working_directory=str(self.directory),timeout_seconds=10,
                environment=dict(PYTHONPATH=str(ROOT), UAV_RAG_URL=self.url, UAV_RAG_TOKEN_FILE=str(token_path))))
            task = dict(task_id='a'*32,scenario='边界任务' * 4000)
            runner.start(task)
            for thread in list(runner.threads.values()):
                thread.join(timeout=20)
            result = runner.lookup(task['task_id'])
            self.assertEqual(result['state'],'completed',result)
            self.assertEqual(result['selection']['target']['suite'],'SM4-GCM')
            self.assertEqual(runner.start(task),result)  # Original task idempotency remains.
        finally:
            if runner:
                runner.close()
            for k,v in previous.items():
                if v is None:
                    os.environ.pop(k,None)
                else:
                    os.environ[k] = v
            sys.path.remove(source)


if __name__ == '__main__':
    unittest.main()

