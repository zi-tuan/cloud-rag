"""云边共同登记的防护策略；模型无权修改期限、配额或可信状态。"""
from shared.communication.envelope import ProtocolError

PROFILES = {
    'lightweight': {'session_ttl': 900, 'key_ttl': 900, 'business_per_minute': 600},
    'enhanced': {'session_ttl': 120, 'key_ttl': 120, 'business_per_minute': 180},
}


def protection_policy(name):
    if not isinstance(name, str) or name not in PROFILES:
        raise ProtocolError('unregistered protection profile')
    return dict(PROFILES[name])
