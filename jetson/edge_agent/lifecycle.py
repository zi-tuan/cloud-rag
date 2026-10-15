"""在控制任务串行区内回收闲置工作负载；不阻塞遥测和同模式轮换。"""
import threading

from shared.communication.envelope import ProtocolError


def retire_unused(state, runner, catalog):
    """调用方必须排除并发的候选准备；周期轮换和恢复只能保持原组合。"""
    with state.lock:
        latest = state.latest()
        if latest and (latest.get('uncertain') or latest['state'] not in ('confirmed', 'aborted')
                or state.clock() < latest.get('retire_at', float('inf'))):
            return {'state': 'waiting_transition'}
        state.sync_codec()
        epoch = state.active()
        policy, suite = state.policy(epoch), state.suite(epoch)
    # 缩容等待可能超过 30 秒，不能持有会话锁，否则增强会话无法及时轮换。
    if runner.authentication(policy, exclusive=True) != {'auth_policy': policy, 'exclusive': True}:
        raise ProtocolError('old authentication deployments were not stopped')
    retained = catalog.for_suite(suite)['id']
    for entry in catalog.public():
        if entry['id'] != retained:
            result = runner.retire(entry['id'])
            if result != {'service_id': entry['id'], 'stopped': True}:
                raise ProtocolError('old service deactivation was not confirmed')
    return {'state': 'reconciled', 'auth_policy': policy, 'service_id': retained}


class WorkloadLifecycle:
    def __init__(self, state, runner, catalog, journal):
        self.state, self.runner, self.catalog, self.journal = state, runner, catalog, journal
        self.lock = threading.RLock()
        self.next_check = 0

    def control(self, action):
        with self.lock:
            return action()

    def once(self):
        if not self.lock.acquire(blocking=False):
            return {'state': 'busy'}
        try:
            # Pi 可能跨多个轮询周期拉起服务；持久准备记录在重启后仍保护候选。
            for record in self.journal.records():
                if record.get('preparation') and not record.get('pending'):
                    return {'state': 'waiting_preparation'}
                pending = record.get('pending')
                if pending:
                    try:
                        transition = self.state.get(pending['request_id'])
                    except ProtocolError:
                        return {'state': 'waiting_preparation'}
                    if (transition.get('uncertain') or transition['state'] not in ('confirmed', 'aborted')
                            or self.state.clock() < transition.get('retire_at', float('inf'))):
                        return {'state': 'waiting_preparation'}
                if record.get('stage') in ('invoking', 'selected', 'switching'):
                    return {'state': 'waiting_preparation'}
            if self.state.clock() < self.next_check:
                return {'state': 'idle'}
            result = retire_unused(self.state, self.runner, self.catalog)
            if result['state'] == 'reconciled':
                self.next_check = self.state.clock() + 30
            return result
        finally:
            self.lock.release()
