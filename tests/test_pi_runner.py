import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared.communication.envelope import ProtocolError
from jetson.edge_agent.executor import ExecutionJournal
from jetson.pi_runner.app import PiRunner


class PiRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        (self.path / 'receipts').mkdir()
        ExecutionJournal(self.path / 'runs.db', initialize=True)
        self.task = {'task_id': 'a' * 32, 'scenario': '切换 SM4'}

    def runner(self, script, timeout=5):
        runner = PiRunner({'journal': str(self.path / 'runs.db'), 'receipts': str(self.path / 'receipts'),
                           'command': [sys.executable, '-c', script], 'working_directory': str(self.path),
                           'timeout_seconds': timeout})
        self.addCleanup(runner.close)
        return runner

    def wait(self, runner):
        runner.threads[self.task['task_id']].join(timeout=8)
        self.assertFalse(runner.threads[self.task['task_id']].is_alive())
        return runner.lookup(self.task['task_id'])

    def test_real_subprocess_receipt_and_idempotency(self):
        runner = self.runner("import os,json,pathlib; pathlib.Path(os.environ['UAV_SELECTION_FILE']).write_text(json.dumps({'selection_kind':'combination','target':{'protection_profile': 'lightweight', 'auth_policy':'RSA','suite':'AES-256-GCM'},'credential_leaked':'UAV_SERVICE_TOKEN' in os.environ}))")
        with patch.dict(os.environ, {'UAV_SERVICE_TOKEN': 'must-not-leak'}):
            self.assertEqual(runner.start(self.task)['state'], 'running')
            result = self.wait(runner)
        self.assertEqual(result['state'], 'completed')
        self.assertFalse(result['selection']['credential_leaked'])
        self.assertEqual(runner.start(self.task), result)
        self.assertEqual(len(runner.threads), 1)
        with self.assertRaises(ProtocolError):
            runner.start({**self.task, 'scenario': '不同内容'})

    def test_model_stdout_is_not_execution_evidence(self):
        runner = self.runner("print('任务成功，已经切换 SM4')")
        runner.start(self.task)
        result = self.wait(runner)
        self.assertEqual(result['state'], 'failed')
        self.assertIn('任务成功', result['model_reply'])

    def test_cluster_connection_environment_reaches_model_tools(self):
        runner = self.runner("import os; print(os.environ['KUBERNETES_SERVICE_HOST']); print(os.environ['KUBERNETES_SERVICE_PORT']); print('Bearer sensitive-value')")
        with patch.dict(os.environ, {'KUBERNETES_SERVICE_HOST': '10.43.0.1', 'KUBERNETES_SERVICE_PORT': '443'}):
            runner.start(self.task)
            result = self.wait(runner)
        self.assertIn('10.43.0.1', result['model_reply'])
        self.assertNotIn('sensitive-value', result['model_reply'])

    def test_timeout_terminates_process_without_reinvocation(self):
        runner = self.runner('import time; time.sleep(60)', timeout=0.1)
        runner.start(self.task)
        self.assertEqual(self.wait(runner)['state'], 'failed')
        self.assertEqual(runner.start(self.task)['state'], 'failed')

    def test_missing_live_handle_is_unknown_and_blocks_next_run(self):
        runner = self.runner('pass')
        runner.journal.save(self.task['task_id'], {'state': 'running', 'task_digest': 'persisted-before-crash'})
        self.assertEqual(runner.lookup(self.task['task_id'])['state'], 'unknown')
        with self.assertRaises(ProtocolError):
            runner.start({'task_id': 'b' * 32, 'scenario': '另一个任务'})
        self.assertEqual(runner.threads, {})

    def test_explicit_target_still_invokes_agent(self):
        runner = self.runner("import os,json,pathlib,sys; prompt=sys.stdin.read(); print('agent invoked'); pathlib.Path(os.environ['UAV_SELECTION_FILE']).write_text(json.dumps({'selection_kind':'combination','target':{'protection_profile': 'lightweight', 'auth_policy':'RSA','suite':'AES-256-GCM'}}))")
        runner.config['mcp_guard_config'] = 'configured'
        task = {**self.task, 'scenario': '切换到 RSA + AES'}
        runner.start(task)
        result = self.wait(runner)
        self.assertEqual(result['state'], 'completed')
        self.assertIn('agent invoked', result['model_reply'])
        self.assertEqual(result['selection']['target'], {'protection_profile': 'lightweight', 'auth_policy': 'RSA', 'suite': 'AES-256-GCM'})

    def test_service_receipt_without_agent_authentication_decision_fails(self):
        runner = self.runner("import os,json,pathlib; pathlib.Path(os.environ['UAV_SELECTION_FILE']).write_text(json.dumps({'service_id':'aes-gcm-v1','ready':True,'suite':'AES-256-GCM'}))")
        runner.start({**self.task, 'scenario': '现在无人机在美国，切换到符合美国的算法'})
        result = self.wait(runner)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['reason'], 'missing_agent_combination_decision')

    def test_mission_receipt_preserves_independent_stage_algorithms(self):
        current = {'auth_policy': 'SM2', 'suite': 'SM4-GCM', 'protection_profile': 'lightweight'}
        plan = {'rationale': 'Compare registered candidates for each stage', 'phases': [
            {'stage': 'transit', 'target': current, 'reason': 'Retain current transit combination'},
            {'stage': 'risk', 'target': {'auth_policy': 'RSA', 'suite': 'AES-256-GCM',
             'protection_profile': 'enhanced'}, 'reason': 'Select another registered combination for risk'},
            {'stage': 'exit', 'target': current, 'reason': 'Restore transit combination after stable exit'},
        ]}
        receipt = {'selection_kind': 'mission_plan', 'plan': plan}
        script = (
            "import os,json,pathlib,sys; prompt=sys.stdin.read(); "
            "pathlib.Path('prompt.txt').write_text(prompt, encoding='utf-8'); "
            "pathlib.Path(os.environ['UAV_SELECTION_FILE']).write_text("
            f"json.dumps({receipt!r}), encoding='utf-8')"
        )
        runner = self.runner(script)
        runner.start({**self.task, 'scenario': 'Plan protection for route A-C-B',
                      'current_combination': current, 'protection_context': {'current_protection': current}})
        result = self.wait(runner)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['selection']['plan'], plan)
        prompt = (self.path / 'prompt.txt').read_text(encoding='utf-8')
        context = json.loads(prompt.split('\n上下文：\n', 1)[1].split('\n证据与解释约束：', 1)[0])
        self.assertEqual(context['current_protection'], current)


if __name__ == '__main__':
    unittest.main()
