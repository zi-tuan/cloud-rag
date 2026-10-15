"""Real local HTTP → PiRunner → substitute Agent → stdio MCP → original Guard.

Original TaskExecutor/SwitchCoordinator run with in-process cloud and readiness fixtures.
No physical device, container, real model or cluster is implied.
"""
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
from uav_rag.server import make_server
from uav_rag.index import KnowledgeBase
from uav_rag.client import RagClient
from jetson.pi_runner.app import PiRunner
from jetson.edge_agent.executor import ExecutionJournal

ROOT = Path(__file__).resolve().parents[1]

def run():
    kb = KnowledgeBase(ROOT/'data/openssl.sqlite3')
    token = secrets.token_urlsafe(32)
    server = make_server(kb, port=0, token=token)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f'http://127.0.0.1:{server.server_port}'
    runner = None
    fixture = None
    try:
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory/'receipts').mkdir()
            (directory/'token').write_text(token, encoding='utf-8')
            ExecutionJournal(directory/'runner.db', initialize=True)
            config = dict(journal=str(directory/'runner.db'), receipts=str(directory/'receipts'),
                          command=[sys.executable, '-m', 'tools.fixture_agent'], working_directory=str(ROOT),
                          timeout_seconds=45, environment={'PYTHONPATH': str(ROOT), 'UAV_RAG_URL': url,
                                                           'UAV_RAG_TOKEN_FILE': str(directory/'token')})
            runner = PiRunner(config)
            # Use the actual task created by the existing local cloud TaskStore fixture.
            sys.path.insert(0, str(ROOT/'tests'))
            from test_executor import ExecutorTests
            fixture = ExecutorTests('test_task_end_to_end_requires_mcp_receipt_and_cloud_verification')
            fixture.setUp()
            task = fixture.task
            scenario = task['scenario']
            runner.start(task)
            runner.threads[task['task_id']].join(timeout=50)
            outcome = runner.lookup(task['task_id'])
            assert outcome['state'] == 'completed', outcome
            trace = json.loads((directory/'receipts'/task['task_id']/'selection.trace.json').read_text(encoding='utf-8'))
            assert scenario in trace['prompt']
            # Existing regression fixture exercises the original deterministic classes.
            fixture.runner.start = lambda task: outcome
            fixture.runner.lookup = lambda task_id: outcome
            executor_result = fixture.complete()
            assert executor_result['state'] == 'succeeded', executor_result
            assert executor_result['task_id'] == task['task_id']
            with RagClient(url, token).request('/health') as response:
                health = json.load(response)
            report = {'scope': 'local real HTTP and stdio; substitute Agent and simulated cluster/readiness',
                      'task': task, 'endpoint': url, 'health': health, 'knowledge': kb.metadata,
                      'runner_outcome': outcome, 'agent_trace': trace,
                      'executor': {'result': executor_result, 'mode': 'original classes with simulated external collaborators'},
                      'unverified': ['real model/Pi/Aivo adapter', 'Linux container', 'real cloud/edge network', 'Kubernetes/K3s', 'flight hardware']}
            output = ROOT/'data/local-integration.json'
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'report': str(output), 'query_pages': len(trace['pages']),
                              'runner': outcome['state'], 'executor_simulation': executor_result['state']}, indent=2))
            return report
    finally:
        if runner:
            runner.close()
        if fixture:
            fixture.doCleanups()
            sys.path.remove(str(ROOT/'tests'))
        server.shutdown()
        server.server_close()
        worker.join()

if __name__ == '__main__':
    run()
