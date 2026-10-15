"""可信结果与通信身份相互独立；disabled 永远不表示 passed。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TrustAdapter:
    enabled: bool = False
    measure: object = None

    def status(self):
        if not self.enabled:
            return 'unknown'
        if self.measure is None:
            return 'unknown'
        try:
            result = self.measure()
        except Exception:
            return 'unknown'
        return result if result in ('passed', 'failed', 'unknown') else 'unknown'
