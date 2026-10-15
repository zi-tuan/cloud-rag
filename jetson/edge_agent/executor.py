"""串行任务执行与重启恢复；Pi 只在新任务第一次执行时启动。"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from shared.communication.envelope import ProtocolError, canonical
from shared.communication.targets import validate_target, explicit_combination


class ExecutionJournal:
    def __init__(self, path, *, initialize=False):
        self.path = str(path)
        if not Path(path).is_file() and not initialize:
            raise ProtocolError('execution journal missing; restore before starting')
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS executions (task_id TEXT PRIMARY KEY, data TEXT NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def get(self, task_id):
        with self.connect() as db:
            row = db.execute('SELECT data FROM executions WHERE task_id=?', (task_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def save(self, task_id, record):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO executions VALUES (?,?)', (task_id, canonical(record).decode()))

    def records(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM executions')]


class TaskExecutor:
    def __init__(self, client, coordinator, catalog, runner, journal):
        self.client, self.coordinator, self.catalog = client, coordinator, catalog
        self.runner, self.journal = runner, journal
        self.lock = threading.Lock()

    def _event(self, task, state, reason=None):
        return self.client.exchange('task.event', {'task_id': task['task_id'],
                                    'event_id': task['task_id'] + ':' + state, 'state': state,
                                    'result': None if reason is None else {'reason': reason}})

    def validate_selection(self, result):
        if isinstance(result, dict) and result.get('selection_kind') == 'combination':
            target = validate_target(result.get('target'))
            if hasattr(self.runner, 'authentication'):
                self.runner.authentication(target['auth_policy'])
            if not self.coordinator.auth_ready(target['auth_policy']):
                raise ProtocolError('target authentication service is not registered or ready')
            suite = target['suite'] or self.coordinator.state.suite(self.coordinator.state.active())
            entry = self.catalog.for_suite(suite)
            self.catalog.get(entry['id'], for_activation=True)
            return {**entry, 'auth_policy': target['auth_policy'], 'protection_profile': target['protection_profile']}
        raise ProtocolError('missing registered protection combination decision')

    def retire(self, service_id):
        result = self.runner.retire(service_id)
        if result != {'service_id': service_id, 'stopped': True}:
            raise ProtocolError('old service deactivation was not confirmed')

    def once(self):
        if not self.lock.acquire(blocking=False):
            return {'state': 'busy'}
        try:
            # 未确定的会话先对账，避免用错误 epoch 领取另一个任务。
            pending = self.coordinator.state.latest()
            if pending and pending.get('uncertain'):
                reconciled = self.coordinator.resume(pending['binding']['request_id'])
                if reconciled['state'] == 'unknown':
                    return reconciled
            claim = self.client.exchange('task.claim', {})
            task = claim['task']
            if task is None:
                return {'state': 'idle'}
            identifier = task['task_id']
            record = self.journal.get(identifier)
            if record is None:
                # 必须在启动 Pi 前落盘；即使启动应答丢失，也不能重新调用模型。
                record = {'stage': 'invoking', 'task': task, 'selection': None}
                self.journal.save(identifier, record)
                epoch = self.coordinator.state.active()
                decision_task = {**task, 'current_combination': {
                    'auth_policy': self.coordinator.state.policy(epoch), 'suite': self.coordinator.state.suite(epoch),
                    'protection_profile': self.coordinator.state.profile(epoch)}}
                outcome = self.runner.lookup(identifier) if claim['reconcile_required'] else self.runner.start(decision_task)
            elif record['stage'] == 'invoking':
                outcome = self.runner.lookup(identifier)
            else:
                outcome = None
            if record['stage'] == 'invoking':
                if outcome is None or outcome.get('state') in ('running', 'unknown'):
                    return {'state': 'unknown', 'task_id': identifier, 'reason': 'reconcile_pi_run'}
                if outcome.get('model_reply'):
                    self.client.exchange('task.event', {'task_id': identifier,
                        'event_id': identifier + ':model-reply', 'state': 'running',
                        'result': {'model_reply': outcome['model_reply']}})
                if outcome.get('state') == 'failed':
                    record.update(stage='failed', reason='pi_selection_or_activation_failed')
                else:
                    try:
                        entry = self.validate_selection(outcome.get('selection'))
                    except ProtocolError:
                        if hasattr(self.runner, 'authentication'):
                            self.runner.authentication(self.coordinator.state.policy(self.coordinator.state.active()), exclusive=True)
                        record.update(stage='failed', reason='invalid_service_selection')
                    else:
                        expected = explicit_combination(task['scenario'])
                        if expected and (entry.get('auth_policy') != expected['auth_policy'] or
                                         (expected['suite'] is not None and entry['suite'] != expected['suite'])):
                            raise ProtocolError('selected combination differs from explicit task target')
                        record.update(stage='selected', selection=entry)
                self.journal.save(identifier, record)
            if record['stage'] == 'failed':
                self._event(task, 'failed', record['reason'])
                return {'state': 'failed', 'task_id': identifier}
            if record['stage'] == 'selected':
                old_suite = self.coordinator.state.suite(self.coordinator.state.active())
                record['old_service_id'] = self.catalog.for_suite(old_suite)['id']
                self._event(task, 'verifying')
                record['stage'] = 'switching'
                self.journal.save(identifier, record)
            if record['stage'] == 'switching':
                # epoch 与任务编号绑定，重启后不生成另一个候选会话。
                arguments = (task['request_id'], 'task-' + identifier, record['selection']['suite'])
                result = self.coordinator.start(*arguments, protection_profile=record['selection'].get('protection_profile', self.coordinator.state.profile(self.coordinator.state.active())), **({'auth_policy': record['selection']['auth_policy']} if 'auth_policy' in record['selection'] else {}))
                if result['state'] == 'unknown':
                    return {'state': 'unknown', 'task_id': identifier, 'reason': 'reconcile_switch'}
                if result['state'] == 'failed':
                    record.update(stage='aborted', reason='target_switch_not_effective')
                    self.journal.save(identifier, record)
                else:
                    record['stage'] = 'confirmed'
                    self.journal.save(identifier, record)
            if record['stage'] in ('confirmed', 'aborted'):
                transition = self.coordinator.state.get(task['request_id'])
                self.coordinator.state.sync_codec()
                if self.coordinator.state.clock() < transition['retire_at']:
                    return {'state': 'verifying', 'task_id': identifier, 'reason': 'transition_grace_active'}
            if record['stage'] == 'aborted':
                if hasattr(self.runner, 'authentication'):
                    self.runner.authentication(self.coordinator.state.policy(self.coordinator.state.active()), exclusive=True)
                if record['selection']['id'] != record['old_service_id']:
                    self.retire(record['selection']['id'])
                record['stage'] = 'failed'
                self.journal.save(identifier, record)
                self._event(task, 'failed', record['reason'])
                return {'state': 'failed', 'task_id': identifier}
            if record['stage'] == 'confirmed':
                from jetson.edge_agent.lifecycle import retire_unused
                if retire_unused(self.coordinator.state, self.runner, self.catalog)['state'] != 'reconciled':
                    return {'state': 'verifying', 'task_id': identifier, 'reason': 'transition_grace_active'}
                record['stage'] = 'retired'
                self.journal.save(identifier, record)
            if record['stage'] == 'retired':
                result = self.client.exchange('task.complete', {'task_id': identifier})
                if result['state'] != 'succeeded':
                    raise ProtocolError('cloud did not confirm task completion')
                record['stage'] = 'completed'
                self.journal.save(identifier, record)
                return {'state': 'succeeded', 'task_id': identifier}
            return {'state': 'unknown', 'task_id': identifier}
        finally:
            self.lock.release()
