import copy
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import Mock

import yaml
from deploy.scripts.rag_config import configure
from uav_rag.client import RagClient
from uav_rag.index import KnowledgeBase
from uav_rag.server import make_server
from uav_rag.tools import RagTools, KnowledgeError
from uav_rag.mcp_guard import guard_class
from jetson.edge_agent.mcp_guard import GuardTools
from tools.fixture_guard import SimulatedOrchestrator

ROOT = Path(__file__).resolve().parents[1]

class FusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(KnowledgeBase(ROOT/'data/openssl.sqlite3'), port=0, token='t'*40)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_guard_rejects_forged_and_cross_task_citations_before_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = SimulatedOrchestrator()
            orchestrator.activate = Mock()
            guard = guard_class(GuardTools)(orchestrator, Path(directory)/'selection.json', rag_client=RagClient(self.url, 't'*40))
            with self.assertRaises(KnowledgeError):
                guard.invoke('uav_service_activate', {'service_id': 'sm4-gcm-v1'})
            target = dict(auth_policy='Ed25519', suite='SM4-GCM', protection_profile='lightweight')
            ids = []
            for sid in ('auth-ed25519', 'sm4-gcm-v1'):
                page = guard.invoke('uav_knowledge_query', {'query': '用途 限制', 'candidate_services': [sid]})
                ids.extend(i['id'] for i in page['items'] if i['kind'] == 'citation')
            with self.assertRaises(KnowledgeError):
                guard.invoke('uav_combination_select', {**target, 'citation_ids': ['forged']})
            other = guard_class(GuardTools)(orchestrator, rag_client=RagClient(self.url, 't'*40))
            with self.assertRaises(KnowledgeError):
                other.invoke('uav_combination_select', {**target, 'citation_ids': ids})
            orchestrator.activate.assert_not_called()
            result = guard.invoke('uav_combination_select', {**target, 'citation_ids': ids})
            self.assertTrue(result['requires_execution_verification'])
            self.assertEqual(json.loads((Path(directory)/'selection.json').read_text())['target'], target)

    def test_failure_and_budget_do_not_record_evidence(self):
        client = RagClient(self.url, 'incorrect')
        tool = RagTools(client, max_calls=2)
        args = {'query': '用途', 'candidate_services': ['auth-sm2']}
        for _ in range(2):
            with self.assertRaises(HTTPError) as caught:
                tool.invoke('uav_knowledge_query', args)
            self.assertEqual(caught.exception.code, 401)
        with self.assertRaises(KnowledgeError):
            tool.invoke('uav_knowledge_query', args)
        self.assertEqual(tool.pages, [])
        # Actual refused local TCP connection, not an HTTP success substitute.
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        disconnected = RagTools(RagClient(f'http://127.0.0.1:{port}', timeout=.1))
        with self.assertRaises(URLError):
            disconnected.invoke('uav_knowledge_query', args)
        self.assertFalse(disconnected.pages)
        # Transport timeout path uses a deliberately nonresponsive local listener.
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            slow = RagTools(RagClient(f'http://127.0.0.1:{listener.getsockname()[1]}', timeout=.05))
            with self.assertRaises((TimeoutError, URLError)):
                slow.invoke('uav_knowledge_query', args)
            self.assertFalse(slow.pages)

    def test_render_config_inherits_task_env_and_mounts_readonly_secret(self):
        docs = list(yaml.safe_load_all((ROOT/'deploy/jetson/edge-control-k8s.yaml').read_text(encoding='utf-8')))
        configure(docs, 'https://rag.example.test')
        cm = next(d for d in docs if d['kind'] == 'ConfigMap')
        runner = json.loads(cm['data']['pi-runner.json'])
        self.assertEqual(runner['environment']['UAV_RAG_URL'], 'https://rag.example.test')
        mcp = json.loads(cm['data']['.mcp.json'])['mcpServers']['uav-services']
        self.assertEqual(mcp['args'], ['-m', 'uav_rag.mcp_guard'])
        self.assertFalse({'UAV_SELECTION_FILE', 'UAV_RAG_URL', 'UAV_RAG_TOKEN_FILE'} & mcp['env'].keys())
        pod = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'pi-runner')['spec']['template']['spec']
        self.assertEqual(next(v for v in pod['volumes'] if v['name'] == 'rag-client')['secret']['defaultMode'], 0o440)
        with self.assertRaises(ValueError):
            configure(docs, 'http://remote.example.test:8787')

    def test_edge_image_contains_no_server_or_database(self):
        from deploy.scripts.build_offline_images import layout
        sources = [s for s, _ in layout('pi-runner')['copies']]
        self.assertIn('uav_rag/client.py', sources)
        self.assertNotIn('uav_rag/server.py', sources)
        self.assertFalse(any(s.startswith(('sources/', 'data/')) for s in sources))
