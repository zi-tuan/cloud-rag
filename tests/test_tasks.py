import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from cloud.cloud_api.tasks import TaskConflict, TaskStore


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'tasks.db'
        self.store = TaskStore(self.path)

    def test_submit_idempotency_and_conflict(self):
        task = self.store.submit('edge', 'request', '切换国密')
        self.assertEqual(task, self.store.submit('edge', 'request', '切换国密'))
        with self.assertRaises(TaskConflict):
            self.store.submit('edge', 'request', '另一任务')

    def test_concurrent_claim_has_one_execution_and_restart_reconciles(self):
        first = self.store.submit('edge', 'a', 'first')
        self.store.submit('edge', 'b', 'second')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: TaskStore(self.path).claim('edge'), range(16)))
        self.assertEqual(sum(not item['reconcile_required'] for item in claims), 1)
        self.assertEqual({item['task']['task_id'] for item in claims}, {first['task_id']})
        self.assertTrue(TaskStore(self.path).claim('edge')['reconcile_required'])

    def test_unknown_blocks_next_task_and_disconnect_does_not_fail(self):
        task = self.store.submit('edge', 'a', 'first')
        self.store.submit('edge', 'b', 'second')
        self.store.claim('edge')
        self.store.heartbeat('edge', {'epoch': 'initial'}, now=100)
        self.assertFalse(self.store.device('edge', now=121)['online'])
        self.assertEqual(self.store.get(task['task_id'])['state'], 'running')
        self.store.event('edge', task['task_id'], 'e1', 'unknown')
        self.assertEqual(self.store.claim('edge')['task']['task_id'], task['task_id'])

    def test_event_idempotency_ownership_and_no_unverified_success(self):
        task_id = self.store.submit('edge', 'a', 'first')['task_id']
        self.store.claim('edge')
        self.store.event('edge', task_id, 'e1', 'verifying')
        self.store.event('edge', task_id, 'e1', 'verifying')
        with self.assertRaises(TaskConflict):
            self.store.event('edge', task_id, 'e1', 'failed')
        with self.assertRaises(TaskConflict):
            self.store.event('edge', task_id, 'e2', 'succeeded', {'verified': True})
        with self.assertRaises(KeyError):
            self.store.event('other', task_id, 'e3', 'failed')
        self.store.event('edge', task_id, 'e4', 'failed')
        with self.assertRaises(TaskConflict):
            self.store.event('edge', task_id, 'e5', 'running')


if __name__ == '__main__':
    unittest.main()
