"""常见自然语言切换命令的确定性目标解析，复杂任务仍交给模型选择。"""
import re

from shared.communication.envelope import ProtocolError


def explicit_combination(scenario):
    text = re.sub(r'\s+', '', scenario)
    if not re.search('切换|改为|更换', text) or any(word in text for word in ('如果', '若', '不要', '不支持', '比较', '或者', '或', '先', '再')):
        return None
    policies = {policy for pattern, policy in ((r'RSA(?:-PSS(?:/SHA-256)?)?', 'RSA'), (r'SM2(?:/SM3)?', 'SM2'), (r'Ed25519', 'Ed25519'))
                if re.search(pattern, text, re.IGNORECASE)}
    suites = {suite for pattern, suite in ((r'AES(?:-256-GCM)?', 'AES-256-GCM'), (r'SM4(?:-GCM)?', 'SM4-GCM'))
              if re.search(pattern, text, re.IGNORECASE)}
    if len(policies) > 1 or len(suites) > 1:
        return None
    if not policies:
        return None
    remainder = re.sub(r'RSA(?:-PSS(?:/SHA-256)?)?|SM2(?:/SM3)?|Ed25519|AES(?:-256-GCM)?|SM4(?:-GCM)?', '', text, flags=re.IGNORECASE)
    for word in ('保持当前加密算法不变', '保持当前通信加密不变', '保持当前身份认证不变', '保持', '不变', '身份认证', '认证算法',
                 '加密算法', '通信加密', '切换到', '切换为', '切换', '改为', '更换', '当前', '认证', '通信', '加密', '算法', '请', '将', '到', '为', '和', '与'):
        remainder = remainder.replace(word, '')
    if remainder.strip('，,。.!！+＋、；;:：'):
        return None
    return {'auth_policy': next(iter(policies)), 'suite': next(iter(suites)) if suites else None}


def validate_target(selection):
    from shared.communication.auth_protocol import POLICIES
    from shared.communication.envelope import SUITES
    from shared.communication.protection import protection_policy
    if not isinstance(selection, dict) or set(selection) != {'auth_policy', 'suite', 'protection_profile'}:
        raise ProtocolError('invalid combination target')
    if selection['auth_policy'] not in POLICIES or (selection['suite'] is not None and selection['suite'] not in SUITES):
        raise ProtocolError('unsupported target combination')
    protection_policy(selection['protection_profile'])
    return selection
