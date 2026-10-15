"""周期轮换复用切换事务，不调用模型，不通过认证延长旧 epoch。"""
import uuid
import time
from shared.communication.envelope import ProtocolError


class EpochRotation:
    def __init__(self, coordinator, auth):
        self.coordinator, self.auth = coordinator, auth
        self.paused = False
        self.next_recovery_poll = 0

    def once(self):
        state = self.coordinator.state
        with state.lock:
            state.sync_codec()
            latest = state.latest()
            epoch = state.active()
            if latest and latest.get('recovery') and (latest.get('uncertain') or latest['state'] not in ('confirmed', 'aborted')):
                if time.monotonic() < self.next_recovery_poll:
                    return {'state': 'paused', 'reason': 'waiting_recovery_reconciliation'}
                self.next_recovery_poll = time.monotonic() + 10
                return self.coordinator.recover_expired()
            try:
                session = self.auth.for_epoch(epoch)
            except ProtocolError:
                if epoch in self.auth.active:
                    self.paused = True
                    if time.monotonic() < self.next_recovery_poll:
                        return {'state': 'paused', 'reason': 'waiting_operator_recovery'}
                    self.next_recovery_poll = time.monotonic() + 10
                    return self.coordinator.recover_expired()
                return {'state': 'awaiting_initial_authentication'}
            if latest and (latest.get('uncertain') or latest['state'] not in ('confirmed', 'aborted')):
                return self.coordinator.resume(latest['binding']['request_id'])
            remaining = min(session['session_expires'], session['key_expires']) - self.auth.clock()
            if remaining > 30:
                return {'state': 'idle'}
            if latest and self.auth.clock() < latest.get('retire_at', 0):
                return {'state': 'waiting_transition'}
            request = 'rotate-' + epoch
            result = self.coordinator.start(request, 'epoch-' + uuid.uuid5(uuid.NAMESPACE_URL, request).hex,
                state.suite(epoch), auth_policy=state.policy(epoch), protection_profile=state.profile(epoch))
            return {**result, 'source': 'periodic_rotation'}
