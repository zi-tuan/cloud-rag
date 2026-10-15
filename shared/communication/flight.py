"""操作员仿真飞行契约；不进入模型工具集。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shared.communication.envelope import ProtocolError
from shared.communication.mission import distance, risk_context, preparation_window


class FlightRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    device_id: str
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_.:-]{1,128}$')
    action: Literal['route', 'takeoff', 'land', 'hold', 'velocity']
    mission_id: str | None = None
    revision: int | None = Field(default=None, ge=1)
    altitude_m: float = Field(default=15, ge=5, le=100)
    velocity: list[float] | None = Field(default=None, min_length=3, max_length=3)
    wait_before_risk: bool = False

    @model_validator(mode='after')
    def action_fields(self):
        if self.wait_before_risk and self.action != 'route':
            raise ValueError('risk waiting requires route action')
        if (self.action == 'route') != (self.mission_id is not None and self.revision is not None):
            raise ValueError('route requires a mission and revision')
        if self.action != 'route' and (self.mission_id is not None or self.revision is not None):
            raise ValueError('mission fields require route action')
        if (self.action == 'velocity') != (self.velocity is not None):
            raise ValueError('velocity vector required only for velocity action')
        if self.velocity and any(abs(v) > 2 for v in self.velocity):
            raise ValueError('velocity limited to 2 m/s for one second')
        return self


def validate_route(mission, sample, profile, now):
    if not sample or not 0 <= now - sample['sampled_at'] <= 5:
        raise ProtocolError('flight_telemetry_stale')
    spec = mission['spec']
    if mission['state'] != 'running' or not mission['plan']:
        raise ProtocolError('flight_protection_plan_not_ready')
    if spec['alarms'] or mission['violations']:
        raise ProtocolError('flight_unresolved_alarm')
    if not 2 <= len(spec['route']) <= 100:
        raise ProtocolError('flight_requires_ordered_waypoints')
    if distance(sample['position'], spec['route'][0]) > 100:
        raise ProtocolError('flight_start_too_far_use_current_position')
    if sum(distance(a, b) for a, b in zip(spec['route'], spec['route'][1:])) > 100000:
        raise ProtocolError('flight_route_exceeds_100km')
    if not 1 <= spec['resources']['cruise_speed_mps'] <= 15:
        raise ProtocolError('flight_speed_outside_1_to_15')
    if any(distance(spec['route'][0], risk['center']) <= risk['radius_m'] for risk in spec['risks']):
        raise ProtocolError('flight_start_inside_risk')
    if any(distance(spec['route'][-1], risk['center']) < risk['radius_m'] + 50 for risk in spec['risks']):
        raise ProtocolError('flight_endpoint_needs_50m_risk_margin')
    risk = risk_context(spec, sample['position'], 0)
    entry = risk['entry_distance_m']
    speed = max(spec['resources']['cruise_speed_mps'], sample.get('speed_mps', 0),
                (mission.get('last_observation') or {}).get('speed_mps', 0))
    lead = preparation_window(mission.get('preparation_durations', []))['lead_seconds']
    if (risk['inside'] or entry is not None and entry / speed <= lead) and profile != 'enhanced':
        raise ProtocolError('flight_wait_for_enhanced_protection')
    # 任务修订时可能已处于提前增强范围；允许已验证的增强保护直接起飞，
    # 不为补齐轻量阶段记录而降级。完整闭环验收仍独立要求所有阶段证据。
    if not any(r['revision'] == mission['revision'] and r.get('workloads_reconciled') is True and (r['stage'] == 'transit' or
               r['stage'] == 'risk' and profile == 'enhanced' and r.get('protection_profile') == 'enhanced'
               and r.get('identity_verified') is True and r.get('new_session_business_verified') is True)
               for r in mission['receipts']):
        raise ProtocolError('flight_baseline_receipt_missing')
    return spec
