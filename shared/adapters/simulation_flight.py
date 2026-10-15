"""独立的操作员仿真飞控，只接受已登记的 192.168.8.110:5762。"""
import time
from shared.communication.envelope import ProtocolError
from shared.communication.mission import circle_entry, distance


class SimulationFlight:
    ENDPOINT = 'tcp:192.168.8.110:5762'

    def __init__(self, endpoint, *, factory=None, clock=time.monotonic, protection_gate=None, preparation_lead=None):
        if endpoint != self.ENDPOINT:
            raise ValueError('manual flight is limited to the registered SITL endpoint')
        if factory is None:
            from pymavlink import mavutil
            factory = mavutil.mavlink_connection
        self.endpoint, self.factory, self.clock = endpoint, factory, clock
        self.link = None
        self.state = 'idle'
        self.deadline = 0
        self.protection_gate = protection_gate or (lambda request: None)
        self.preparation_lead = preparation_lead or (lambda: 30)

    def close(self):
        if self.link:
            self.link.close()
        self.link = None
        self.state = 'idle'

    def begin(self, request, spec):
        self.close()
        self.request, self.spec = request, spec
        self.link = self.factory(self.endpoint, source_system=252, source_component=190, autoreconnect=False)
        self.state, self.deadline = 'connecting', self.clock() + 10
        self.uploaded = set()
        self.target = None
        self.last_heartbeat = float('-inf')
        self.last_gcs = float('-inf')
        self.last_position = None
        self.last_position_at = float('-inf')
        self.current_sequence = None
        self.gates, self.route_sequences = {}, []
        self.evidence = {'endpoint': self.endpoint, 'mission_upload_ack': False, 'command_acks': [], 'reached': [],
                         'mission_requested_sequences': [], 'mission_ack_type': None}

    def command(self, code, params, state):
        self.link.mav.command_long_send(*self.target, code, 0, *params)
        self.expected_command = code
        self.state, self.deadline = state, self.clock() + 15

    def mode(self, value, state):
        self.link.mav.set_mode_send(self.target[0], 1, value)
        self.expected_mode = value
        self.state, self.deadline = state, self.clock() + 15

    def upload(self):
        from pymavlink import mavutil
        m = mavutil.mavlink
        route, altitude = self.spec['route'], self.request['altitude_m']
        # ArduPilot 将第 0 项作为 home；导航从第 1 项开始。
        self.items = [(m.MAV_CMD_NAV_WAYPOINT, route[0], altitude, [0, 2, 0, 0]),
                      (m.MAV_CMD_NAV_TAKEOFF, route[0], altitude, [0, 0, 0, 0]),
                      (m.MAV_CMD_DO_CHANGE_SPEED, route[0], 0, [1, self.spec['resources']['cruise_speed_mps'], -1, 0])]
        for index, point in enumerate(route):
            if index and self.request.get('wait_before_risk'):
                start = route[index - 1]
                length = distance(start, point)
                candidates = []
                for risk in self.spec['risks']:
                    entry = circle_entry(start, point, risk['center'], risk['radius_m'])
                    if entry is None or entry == 0:
                        continue
                    margin = self.spec['resources']['cruise_speed_mps'] * self.preparation_lead() * .7
                    if margin < 50 or entry < 50:
                        raise ProtocolError('flight_risk_wait_point_has_no_margin')
                    offset = max(0, entry - margin)
                    fraction = offset / length
                    gate_point = {key: start[key] + (point[key] - start[key]) * fraction for key in ('lat', 'lon')}
                    if any(distance(gate_point, area['center']) < area['radius_m'] + 50 for area in self.spec['risks']):
                        raise ProtocolError('flight_risk_wait_point_has_no_margin')
                    candidates.append((offset, risk['id'], gate_point))
                for _, risk_id, gate_point in sorted(candidates, key=lambda candidate: candidate[0]):
                    sequence = len(self.items)
                    self.gates[sequence] = {'risk_id': risk_id, 'position': gate_point, 'sequence': sequence}
                    self.items.append((m.MAV_CMD_NAV_LOITER_UNLIM, gate_point, altitude, [0, 0, 0, 0]))
            self.route_sequences.append(len(self.items))
            self.items.append((m.MAV_CMD_NAV_WAYPOINT, point, altitude, [0, 2, 0, 0]))
        self.items.append((m.MAV_CMD_NAV_LOITER_UNLIM, route[-1], altitude, [0, 0, 0, 0]))
        self.evidence['risk_waits'] = []
        self.link.mav.mission_count_send(*self.target, len(self.items))
        self.state, self.deadline = 'uploading', self.clock() + 30

    def send_item(self, sequence, integer):
        from pymavlink import mavutil
        if not 0 <= sequence < len(self.items):
            raise ProtocolError('flight_invalid_mission_request')
        code, point, altitude, params = self.items[sequence]
        frame = mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT if integer else mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT
        send = self.link.mav.mission_item_int_send if integer else self.link.mav.mission_item_send
        lat, lon = (round(point['lat']*1e7), round(point['lon']*1e7)) if integer else (point['lat'], point['lon'])
        send(*self.target, sequence, frame, code, 0, 1, *params, lat, lon, altitude)
        self.uploaded.add(sequence)

    def tick(self):
        from pymavlink import mavutil
        m, now = mavutil.mavlink, self.clock()
        if now > self.deadline:
            raise ProtocolError('flight_timeout_' + self.state)
        if now - self.last_gcs >= 1:
            self.link.mav.heartbeat_send(m.MAV_TYPE_GCS, m.MAV_AUTOPILOT_INVALID, 0, 0, m.MAV_STATE_ACTIVE)
            self.last_gcs = now
        for _ in range(100):
            msg = self.link.recv_match(blocking=False)
            if msg is None:
                break
            kind = msg.get_type()
            if kind == 'HEARTBEAT' and msg.autopilot == m.MAV_AUTOPILOT_ARDUPILOTMEGA and msg.type == m.MAV_TYPE_QUADROTOR:
                source = (msg.get_srcSystem(), msg.get_srcComponent())
                if self.target is not None and source != self.target:
                    continue
                self.target, self.last_heartbeat = source, now
                self.armed = bool(msg.base_mode & m.MAV_MODE_FLAG_SAFETY_ARMED)
                if self.state == 'connecting':
                    self.link.mav.request_data_stream_send(*self.target, m.MAV_DATA_STREAM_ALL, 2, 1)
                    action = self.request['action']
                    if action == 'route':
                        self.upload()
                    elif action == 'land':
                        self.command(m.MAV_CMD_NAV_LAND, [0]*7, 'land_ack')
                    elif action == 'hold':
                        self.mode(5, 'hold_mode')
                    elif action == 'velocity':
                        if not self.armed or msg.custom_mode != 4:
                            raise ProtocolError('flight_velocity_requires_armed_guided')
                        self.velocity_until = now + 1
                        self.state, self.deadline = 'velocity', now + 3
                    else:
                        if self.armed:
                            raise ProtocolError('flight_takeoff_requires_disarmed')
                        self.mode(4, 'guided_mode')
                elif self.state.endswith('_mode') and msg.custom_mode == self.expected_mode:
                    if self.state == 'guided_mode':
                        if self.armed:
                            if self.request['action'] != 'route':
                                raise ProtocolError('flight_already_armed')
                            self.mode(3, 'auto_mode')
                        else:
                            self.command(m.MAV_CMD_COMPONENT_ARM_DISARM, [1, 0, 0, 0, 0, 0, 0], 'arm_ack')
                    elif self.state == 'auto_mode':
                        self.state, self.deadline = 'flying', now + 900
                    else:
                        return {'state': 'completed', 'evidence': self.evidence}
                continue
            if not self.target or (msg.get_srcSystem(), msg.get_srcComponent()) != self.target:
                continue
            if self.state == 'uploading' and kind in ('MISSION_REQUEST', 'MISSION_REQUEST_INT'):
                self.evidence['mission_requested_sequences'].append(msg.seq)
                self.send_item(msg.seq, kind == 'MISSION_REQUEST_INT')
            elif self.state == 'uploading' and kind == 'MISSION_ACK':
                self.evidence['mission_ack_type'] = msg.type
                self.evidence['mission_uploaded_sequences'] = sorted(self.uploaded)
                self.evidence['mission_expected_count'] = len(self.items)
                if msg.type != m.MAV_MISSION_ACCEPTED or len(self.uploaded) != len(self.items):
                    raise ProtocolError('flight_mission_upload_rejected')
                self.evidence['mission_upload_ack'] = True
                self.mode(4, 'guided_mode')
            elif self.state.endswith('_ack') and kind == 'COMMAND_ACK' and msg.command == self.expected_command:
                if msg.result == m.MAV_RESULT_IN_PROGRESS:
                    continue
                if msg.result != m.MAV_RESULT_ACCEPTED:
                    raise ProtocolError('flight_command_rejected_' + str(msg.command))
                self.evidence['command_acks'].append(msg.command)
                if self.state == 'arm_ack':
                    self.command(m.MAV_CMD_NAV_TAKEOFF, [0, 0, 0, 0, 0, 0, self.request['altitude_m']], 'takeoff_ack')
                elif self.state == 'takeoff_ack':
                    self.state, self.deadline = 'climbing', now + 90
                else:
                    self.state, self.deadline = 'landing', now + 120
            elif kind == 'GLOBAL_POSITION_INT':
                self.last_position = {'lat': msg.lat / 1e7, 'lon': msg.lon / 1e7, 'relative_alt_m': msg.relative_alt / 1000}
                self.last_position_at = now
                if self.request.get('wait_before_risk'):
                    self.speed_mps = (msg.vx ** 2 + msg.vy ** 2) ** .5 / 100
                if self.state == 'climbing' and msg.relative_alt / 1000 >= self.request['altitude_m'] * .9:
                    if self.request['action'] == 'route':
                        self.mode(3, 'auto_mode')
                    else:
                        return {'state': 'completed', 'evidence': {**self.evidence, 'position': self.last_position}}
                elif self.state == 'landing' and not self.armed and msg.relative_alt < 1000:
                    return {'state': 'completed', 'evidence': {**self.evidence, 'position': self.last_position}}
            elif kind == 'MISSION_CURRENT':
                self.current_sequence = msg.seq
                if self.state == 'resuming_risk' and msg.seq == self.resume_sequence:
                    self.evidence['risk_waits'][-1]['resume_confirmed_at'] = now
                    self.state, self.deadline = 'flying', now + 900
            elif self.state == 'flying' and kind == 'MISSION_ITEM_REACHED':
                self.evidence['reached'].append(msg.seq)
                final_sequence = self.route_sequences[-1]
                if msg.seq == final_sequence and set(self.route_sequences) <= set(self.evidence['reached']):
                    return {'state': 'completed', 'evidence': {**self.evidence, 'position': self.last_position,
                        'holding_at_endpoint': True, 'waypoint_count': len(self.spec['route']),
                        'completed_waypoints': list(range(len(self.spec['route'])))}}
        if self.target and now - self.last_heartbeat > 5:
            raise ProtocolError('flight_heartbeat_lost')
        gate = self.gates.get(self.current_sequence)
        if gate and self.state == 'flying' and self.last_position and now - self.last_position_at <= 2 and distance(self.last_position, gate['position']) <= 10 and self.speed_mps <= .5:
            self.evidence['risk_waits'].append({**gate, 'stopped_at': now})
            self.state, self.deadline = 'waiting_enhanced', float('inf')
        if self.state == 'waiting_enhanced' and now - self.last_position_at <= 2:
            proof = self.protection_gate(self.request)
            if proof:
                self.evidence['risk_waits'][-1].update(verified_at=now, protection=proof)
                self.resume_sequence = self.current_sequence + 1
                self.link.mav.mission_set_current_send(*self.target, self.resume_sequence)
                self.state, self.deadline = 'resuming_risk', now + 15
        if self.state == 'velocity':
            vector = self.request['velocity'] if now < self.velocity_until else [0, 0, 0]
            self.link.mav.set_position_target_local_ned_send(0, *self.target, m.MAV_FRAME_LOCAL_NED, 0b0000111111000111, 0, 0, 0, *vector, 0, 0, 0, 0, 0)
            if now >= self.velocity_until:
                return {'state': 'completed', 'evidence': self.evidence}
        return {'state': 'running', 'evidence': {**self.evidence, 'stage': self.state}}
