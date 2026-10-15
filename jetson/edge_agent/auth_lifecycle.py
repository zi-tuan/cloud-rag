"""只启停已预置的认证 Deployment；不创建身份、不向模型暴露停用接口。"""
import copy
import time

from shared.communication.auth_protocol import POLICIES
from shared.communication.envelope import ProtocolError
from jetson.edge_agent.catalog import GuardedOrchestrator


class AuthenticationCatalog:
    def __init__(self, entries):
        if set(entries) != POLICIES:
            raise ProtocolError('authentication deployment registry incomplete')
        self.entries = copy.deepcopy(entries)
        for policy, entry in self.entries.items():
            if entry['deployment'] != 'auth-' + policy.lower() or entry['namespace'] != 'uav-mvp':
                raise ProtocolError('authentication deployment registry scope rejected')
            pod = entry['pod_spec']
            if len(pod['containers']) != 1 or pod['containers'][0]['image'] != entry['image']:
                raise ProtocolError('authentication deployment registry image rejected')

    def get(self, policy, *, for_activation=False):
        if policy not in self.entries:
            raise ProtocolError('authentication policy is not registered')
        return self.entries[policy]


class AuthenticationOrchestrator(GuardedOrchestrator):
    @staticmethod
    def validate_deployment(entry, resource):
        GuardedOrchestrator.validate_deployment(entry, resource)
        if not GuardedOrchestrator.matches(entry['pod_spec'], resource['spec']['template']['spec']):
            raise ProtocolError('authentication deployment differs from registered isolation template')

    def provision(self, service_id, entry):
        raise ProtocolError('authentication identity must be provisioned before activation')

    def ensure(self, policy):
        result = self.status(policy)
        if not result['ready']:
            self.activate(policy)
        deadline = time.monotonic() + 120
        while not self.status(policy)['ready']:
            if time.monotonic() >= deadline:
                raise ProtocolError('authentication deployment did not become ready')
            time.sleep(1)
        return {'auth_policy': policy, 'ready': True}

    def retain(self, policy):
        self.ensure(policy)
        for other in sorted(POLICIES - {policy}):
            self.deactivate(other)
        return {'auth_policy': policy, 'exclusive': True}
