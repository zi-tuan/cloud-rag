"""航线任务契约与几何判定；地图坐标为 WGS84，经纬度，不发飞控指令。"""
import math
from pydantic import BaseModel, ConfigDict, Field
from shared.communication.envelope import ProtocolError
from shared.communication.targets import validate_target


def preparation_window(durations):
    """真实增强准备耗时的最近 100 次 P95，加 30 秒余量。无样本默认 30 秒。"""
    if not isinstance(durations, list) or len(durations) > 100 or any(
        type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in durations
    ):
        raise ProtocolError('invalid preparation measurements')
    measured = sorted(durations)
    p95 = measured[math.ceil(len(measured) * .95) - 1] if measured else 0
    return {'default_lead_seconds': 30, 'preparation_p95_seconds': p95,
            'preparation_samples': len(measured), 'lead_seconds': max(30, p95 + 30)}


class Point(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    lat: float = Field(ge=-85, le=85)
    lon: float = Field(ge=-180, le=180)


class RiskArea(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')
    center: Point
    radius_m: float = Field(ge=1, le=50000)


class ResourceConstraints(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    cpu_budget_millicores: int = Field(default=1000, ge=50, le=64000)
    memory_budget_mib: int = Field(default=1024, ge=64, le=65536)
    cruise_speed_mps: float = Field(default=10, gt=0, le=100)


class MissionSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    goal: str = Field(min_length=1, max_length=16000)
    route: list[Point] = Field(min_length=2, max_length=100)
    risks: list[RiskArea] = Field(min_length=1, max_length=20)
    resources: ResourceConstraints = Field(default_factory=ResourceConstraints)
    alarms: list[str] = Field(default_factory=list, max_length=100)


def xy(point, origin):
    return ((point['lon'] - origin['lon']) * 111195 * math.cos(math.radians(origin['lat'])),
            (point['lat'] - origin['lat']) * 111195)


def distance(a, b):
    return math.hypot(*xy(a, b))


def advance_route(route, position, next_waypoint):
    """仅按顺序消费实际到达的航点；闭合航线不能在起点直接判为完成。"""
    while next_waypoint < len(route) and distance(position, route[next_waypoint]) <= 30:
        next_waypoint += 1
    return next_waypoint


def risk_context(spec, position, next_waypoint):
    """到风险入口距离取参考航线的剩余弧长，位置偏航时保守计入当前直线接近。"""
    route, risks = spec['route'], spec['risks']
    boundaries = {r['id']: distance(position, r['center']) - r['radius_m'] for r in risks}
    inside = [name for name, value in boundaries.items() if value <= 0]
    outside = all(value >= 50 for value in boundaries.values())
    path = [position, *route[next_waypoint:]]
    entry = math.inf
    for risk in risks:
        offset = 0
        for a, b in zip(path, path[1:]):
            hit = circle_entry(a, b, risk['center'], risk['radius_m'])
            if hit is not None:
                entry = min(entry, offset + hit)
                break
            offset += distance(a, b)
    return {'inside': inside, 'outside_margin': outside, 'entry_distance_m': None if math.isinf(entry) else entry,
            'boundaries_m': boundaries, 'arrival': next_waypoint == len(route) and distance(position, route[-1]) <= 30}


def segment_projection(p, a, b):
    px, py = xy(p, a)
    bx, by = xy(b, a)
    length = bx * bx + by * by
    t = max(0, min(1, (px * bx + py * by) / length)) if length else 0
    projected = {'lat': a['lat'] + t * (b['lat'] - a['lat']), 'lon': a['lon'] + t * (b['lon'] - a['lon'])}
    return distance(p, projected), projected


def circle_entry(a, b, center, radius):
    ax, ay = xy(a, center)
    bx, by = xy(b, center)
    dx, dy = bx - ax, by - ay
    if ax * ax + ay * ay <= radius * radius:
        return 0
    length = dx * dx + dy * dy
    if not length:
        return None
    dot = ax * dx + ay * dy
    discriminant = dot * dot - length * (ax * ax + ay * ay - radius * radius)
    if discriminant < 0:
        return None
    t = (-dot - math.sqrt(discriminant)) / length
    return t * math.sqrt(length) if 0 <= t <= 1 else None


def validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) != {'phases', 'rationale'} or not isinstance(plan['rationale'], str) or not plan['rationale'].strip():
        raise ProtocolError('invalid mission plan')
    if not isinstance(plan['phases'], list) or len(plan['phases']) != 3:
        raise ProtocolError('three registered protection phases required')
    for phase, stage, profile in zip(plan['phases'], ('transit', 'risk', 'exit'), ('lightweight', 'enhanced', 'lightweight')):
        if not isinstance(phase, dict) or set(phase) != {'stage', 'target', 'reason'} or phase['stage'] != stage or not isinstance(phase['reason'], str) or not phase['reason'].strip():
            raise ProtocolError('invalid protection phase')
        target = validate_target(phase['target'])
        if target['suite'] is None or target['protection_profile'] != profile:
            raise ProtocolError('phase profile differs from protection objective')
    return plan
