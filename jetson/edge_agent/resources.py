"""从容器 cgroup 读取实际 CPU/内存，不向模型提供凭据或宿主控制工具。"""
from pathlib import Path
import time


class ResourceSampler:
    def __init__(self):
        self.previous = None

    def __call__(self):
        now = time.monotonic()
        try:
            cpu = int(Path('/sys/fs/cgroup/cpuacct/cpuacct.usage').read_text()) / 1e9
            memory = int(Path('/sys/fs/cgroup/memory/memory.usage_in_bytes').read_text()) / 1048576
        except (OSError, KeyError, ValueError):
            return {'available': False}
        previous = self.previous
        self.previous = (now, cpu)
        millicores = (cpu - previous[1]) / (now - previous[0]) * 1000 if previous and now > previous[0] else 0
        return {'available': True, 'scope': 'edge_agent_container', 'cpu_millicores': round(millicores, 1), 'memory_mib': round(memory, 1)}


def service_fits(entry, constraints):
    requests = entry['resources']['requests']
    cpu = requests['cpu']
    memory = requests['memory']
    cpu = float(cpu[:-1]) if cpu.endswith('m') else float(cpu) * 1000
    memory = float(memory[:-2]) * (1024 if memory.endswith('Gi') else 1)
    return cpu <= constraints['cpu_budget_millicores'] and memory <= constraints['memory_budget_mib']
