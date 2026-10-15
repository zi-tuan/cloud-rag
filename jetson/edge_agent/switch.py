"""Jetson 发起协调，所有远端状态必须来自认证应答。"""
from __future__ import annotations

import secrets
import math
import hashlib

from shared.communication.envelope import ProtocolError, canonical


class SwitchCoordinator:
    def __init__(self, state, client, ready, auth_ready=None):
        self.state, self.client, self.ready = state, client, ready
        self.auth_ready = auth_ready or (lambda policy: policy == 'Ed25519')

    def business_epoch(self):
        record = self.state.latest()
        if record and record.get('uncertain'):
            raise ProtocolError('reconcile switch before sending ordinary business')
        return self.state.active()

    def recover_expired(self):
        with self.state.lock, self.client.lock:
            old = self.state.active()
            latest = self.state.latest()
            pending = latest and (latest.get('uncertain') or latest['state'] not in ('confirmed', 'aborted'))
            if pending:
                if latest.get('recovery') and latest['state'] == 'committed':
                    return self.resume(latest['binding']['request_id'])
                if latest['state'] not in ('prepared', 'probed'):
                    return {'state': 'paused', 'reason': 'session_expired_manual_recovery_required'}
            nonce = secrets.token_hex(32)
            offer = self.client.auth_request('recovery', {'device_id': self.state.codec.device_id, 'old_epoch': old, 'nonce': nonce})
            binding = offer.get('binding')
            if binding is None:
                return {'state': 'paused', 'reason': 'session_expired_manual_recovery_required'}
            if (binding.get('old_epoch') != old or binding.get('suite') != self.state.suite(old)
                    or binding.get('auth_policy') != self.state.policy(old)
                    or binding.get('protection_profile') != self.state.profile(old)):
                raise ProtocolError('recovery cannot alter registered protection')
            transcript = offer.get('transcript', {})
            expected = {'device_id': self.state.codec.device_id, 'cloud_id': self.client.auth.cloud_id,
                'credential_version': self.client.auth.credential_version, 'key_id': self.state.codec.key_id,
                'policy': binding['auth_policy'], 'epoch': binding['epoch'], 'suite': binding['suite'],
                'protection_profile': binding['protection_profile'], 'client_nonce': nonce,
                'request_id': 'recovery-discovery-' + hashlib.sha256(canonical(binding)).hexdigest()}
            if any(transcript.get(k) != v for k,v in expected.items()):
                raise ProtocolError('recovery discovery binding mismatch')
            if not self.state.clock() < transcript.get('challenge_expires', 0) <= self.state.clock() + 30:
                raise ProtocolError('recovery discovery expired')
            self.client.auth.provider.verify('cloud-challenge', transcript, offer.get('proof'))
            if pending and latest.get('recovery') and latest['binding'] == binding:
                return self.resume(binding['request_id'])
            if pending:
                # 云端签名确认授权恢复的旧 epoch；仅结束未提交候选，已提交记录不能回滚。
                if latest['binding']['old_epoch'] != old:
                    raise ProtocolError('recovery cannot abandon committed transaction')
                latest.update(state='aborted', uncertain=False, retire_at=self.state.clock())
                self.state.save(latest)
            self.state.begin(binding, timeout=120, grace=0, recovery=True)
            return self.resume(binding['request_id'])

    def start(self, request_id, epoch, suite, auth_policy=None, protection_profile='lightweight'):
        try:
            existing = self.state.get(request_id)
        except ProtocolError:
            existing = None
        if existing:
            if existing['binding']['epoch'] != epoch or existing['binding']['suite'] != suite or existing['binding']['protection_profile'] != protection_profile or (auth_policy is not None and self.state.policy(epoch) != auth_policy):
                raise ProtocolError('switch identifier conflict')
            return self.resume(request_id)
        if not self.ready(suite):
            raise ProtocolError('edge target service not ready')
        if auth_policy is not None and not self.auth_ready(auth_policy):
            raise ProtocolError('edge target authentication not ready')
        binding = {'device_id': self.state.codec.device_id, 'request_id': request_id,
                   'old_epoch': self.state.active(), 'epoch': epoch, 'suite': suite, 'protection_profile': protection_profile}
        if auth_policy is not None:
            binding['auth_policy'] = auth_policy
        self.state.begin(binding)
        return self.resume(request_id)

    def call(self, op, record, epoch, **extra):
        response = self.client.exchange_at(op, {'binding': record['binding'], **extra}, epoch)
        result = response['result']
        if not isinstance(result, dict) or result.get('binding') != record['binding']:
            raise ProtocolError('switch response binding mismatch')
        if 'retire_at' not in result:
            raise ProtocolError('switch response retirement deadline missing')
        if result.get('state') in ('committed', 'confirmed', 'aborted'):
            self.retirement_deadline(result['retire_at'])
            if result['state'] in ('committed', 'confirmed') and 'retire_at' in record and record['retire_at'] != result['retire_at']:
                raise ProtocolError('authenticated retirement deadline changed')
        return result

    def remote_status(self, record):
        # 候选尚未在云端建立时只能用旧会话；提交后旧会话可能已回收。
        try:
            return self.call('switch.status', record, record['binding']['epoch'])
        except (OSError, ProtocolError, ValueError):
            return self.call('switch.status', record, record['binding']['old_epoch'])

    def uncertain(self, record):
        record['uncertain'] = True
        self.state.save(record)
        return {'state': 'unknown', 'request_id': record['binding']['request_id'], 'target_effective': False}

    def retirement_deadline(self, value):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ProtocolError('authenticated retirement deadline missing or invalid')
        return value

    def finish(self, record, state, retire_at):
        active = record['binding']['epoch'] if state == 'confirmed' else record['binding']['old_epoch']
        if record['state'] not in ('confirmed', 'aborted'):
            record.update(state=state, uncertain=False, retire_at=self.retirement_deadline(retire_at))
            self.state.save(record, active=active)
        return {'state': 'succeeded' if state == 'confirmed' else 'failed',
                'request_id': record['binding']['request_id'], 'epoch': active,
                'suite': record['binding']['suite'] if state == 'confirmed' else None,
                'protection_profile': self.state.profile(active), 'target_effective': state == 'confirmed'}

    def resume(self, request_id):
        # 完整切换共享认证锁，避免后台上报在阶段之间插队耗尽准备期限。
        with self.state.lock, self.client.lock:
            record = self.state.get(request_id)
            b = record['binding']
            if record['state'] in ('confirmed', 'aborted'):
                return self.finish(record, record['state'], record['retire_at'])
            try:
                try:
                    remote = self.remote_status(record)
                except (OSError, ProtocolError, ValueError):
                    # 同一请求重发 prepare 幂等；不新建任务或换 request_id。
                    if record.get('recovery'):
                        raise ProtocolError('recovery requires authenticated candidate reconciliation')
                    remote = self.call('switch.prepare', record, b['old_epoch'])
                if remote['state'] == 'aborted':
                    return self.finish(record, 'aborted', remote['retire_at'])
                if remote['state'] in ('prepared', 'probed'):
                    if not self.ready(b['suite']) or not self.auth_ready(self.state.policy(b['epoch'])):
                        return self.abort(request_id)
                    if not record['edge_nonce']:
                        record['edge_nonce'] = secrets.token_hex(32)
                        self.state.save(record)
                    probe = self.call('switch.probe', record, b['epoch'], edge_nonce=record['edge_nonce'])
                    if probe.get('edge_nonce') != record['edge_nonce'] or not isinstance(probe.get('cloud_nonce'), str):
                        raise ProtocolError('probe response challenge mismatch')
                    record.update(state='probed', cloud_nonce=probe['cloud_nonce'])
                    self.state.save(record)
                    remote = self.call('switch.commit', record, b['epoch'] if record.get('recovery') else b['old_epoch'],
                                       edge_nonce=record['edge_nonce'], cloud_nonce=record['cloud_nonce'])
                    if remote['state'] == 'aborted':
                        return self.finish(record, 'aborted', remote['retire_at'])
                if remote['state'] not in ('committed', 'confirmed') or remote['active'] != b['epoch']:
                    raise ProtocolError('remote session is not committed')
                if remote['state'] == 'confirmed':
                    if not record.get('business_receipt'):
                        raise ProtocolError('local confirmation evidence missing')
                    return self.finish(record, 'confirmed', remote['retire_at'])
                record.update(state='committed', uncertain=False,
                              retire_at=self.retirement_deadline(remote['retire_at']))
                self.state.save(record, active=b['epoch'])
                # 真实 heartbeat 业务经新会话往返；回执只存在于加密应答体。
                response = self.client.exchange_at('heartbeat', {'switch_request_id': b['request_id'],
                                                               'epoch': b['epoch']}, b['epoch'])
                receipt = response.get('verification_receipt')
                if not isinstance(receipt, str) or len(receipt) != 64:
                    raise ProtocolError('business decryption receipt missing')
                record['business_receipt'] = receipt
                self.state.save(record)
                remote = self.call('switch.confirm', record, b['epoch'], business_receipt=receipt)
                if remote['state'] != 'confirmed' or remote['active'] != b['epoch']:
                    raise ProtocolError('confirmation not committed')
                return self.finish(record, 'confirmed', remote['retire_at'])
            except (OSError, ProtocolError, ValueError):
                return self.uncertain(record)

    def abort(self, request_id):
        with self.state.lock, self.client.lock:
            record = self.state.get(request_id)
            if record['state'] == 'confirmed':
                raise ProtocolError('confirmed switch cannot be silently reverted')
            try:
                try:
                    remote = self.call('switch.abort', record, record['binding']['old_epoch'])
                except (OSError, ProtocolError, ValueError):
                    remote = self.call('switch.abort', record, record['binding']['epoch'])
                if remote['state'] != 'aborted' or remote['active'] != record['binding']['old_epoch']:
                    raise ProtocolError('rollback not confirmed')
                return self.finish(record, 'aborted', remote['retire_at'])
            except (OSError, ProtocolError, ValueError):
                return self.uncertain(record)
