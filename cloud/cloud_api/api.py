"""单设备任务 API。云边业务请求和应答均经过认证加密消息层。"""
from __future__ import annotations

import json
import hmac
import os
import threading
import time
from fastapi import FastAPI, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import uvicorn
from shared.communication.http_api import BoundedBodyMiddleware, document_validation_errors
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from cloud.cloud_api.tasks import TaskConflict, TaskStore
from shared.communication.envelope import MAX_MESSAGE, ProtocolError, canonical
from shared.communication.crypto_client import CryptoServiceCodec
from shared.communication.switch import CloudSwitch, SwitchState
from shared.communication.auth_client import PolicyProviders, AuthenticatedCodec
from shared.communication.targets import explicit_combination
from cloud.cloud_api.auth import CloudAuth
from shared.communication.mission import MissionSpec


class CloudApplication:
    def __init__(self, store, codec, login_verification_url=None, switch=None, *, login_verifier=None, auth=None):
        parsed_url = urllib.parse.urlsplit(login_verification_url or '')
        if login_verifier is None and (parsed_url.scheme not in ('http', 'https') or not parsed_url.hostname
                                       or parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment):
            raise ValueError('login verification URL is required')
        self.store, self.codec = store, codec
        self.auth = auth
        if auth is not None:
            self.codec = AuthenticatedCodec(codec, auth.provider, auth)
        self.login_verification_url, self.login_verifier = login_verification_url, login_verifier
        self.switch = switch
        self.lock = switch.lock if switch else threading.RLock()
        from cloud.cloud_api.quota import DeviceQuota
        self.quota = DeviceQuota(store.path, clock=auth.clock if auth else time.time)
        from cloud.cloud_api.missions import MissionStore
        self.missions = MissionStore(store.path, clock=auth.clock if auth else time.time)
        from cloud.cloud_api.flights import FlightStore
        self.flights = FlightStore(store.path, clock=auth.clock if auth else time.time)
        from cloud.cloud_api.recovery import SessionRecovery
        self.recovery = SessionRecovery(self)
        if switch:
            switch.authorize_binding = lambda binding: self.missions.guard_switch(binding) if switch.state.profile(binding['old_epoch']) == 'enhanced' and binding['protection_profile'] == 'lightweight' else None
            if auth:
                switch.epoch_expires = lambda epoch: auth.for_epoch(epoch)['session_expires']
                switch.expired_deadline = auth.deadline

    def verify_login(self, authorization):
        """通过现有 Web 登录服务校验 Bearer 会话；返回 True/False/None(校验服务不可用)。"""
        if self.login_verifier is not None:
            return bool(self.login_verifier(authorization))
        if not isinstance(authorization, str) or not authorization.startswith('Bearer '):
            return False
        token = authorization[7:]
        if not token or len(token) > 8192 or any(char.isspace() for char in token):
            return False

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        request = urllib.request.Request(self.login_verification_url, headers={
            'Authorization': authorization,
            'Accept': 'application/json',
        })
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=5) as response:
                if response.status != 200:
                    return None if response.status >= 500 else False
                raw = response.read(65537)
                if len(raw) > 65536:
                    return False
                result = json.loads(raw)
                data = result.get('data') if isinstance(result, dict) and result.get('code') == 200 else None
                return isinstance(data, dict) and isinstance(data.get('user'), dict)
        except urllib.error.HTTPError as error:
            return None if error.code >= 500 else False
        except (OSError, ValueError, TypeError):
            return None

    def exchange(self, envelope):
        with self.lock:
            if self.switch:
                self.switch.maintain()
            return self._exchange(envelope)

    def _exchange(self, envelope):
        header, payload = self.codec.open(envelope)
        # 只有认证通过后才产生业务错误应答，禁止明文业务降级。
        try:
            if not isinstance(payload, dict):
                raise ProtocolError('invalid payload')
            device = header['device_id']
            kind = header['kind']
            from shared.communication.protection import protection_policy
            if self.auth:
                profile = self.auth.get(header['auth_session_id'])['protection_profile']
                lane = 'control' if kind.startswith(('switch.', 'auth.', 'mission.')) else 'business'
                self.quota.consume(device, lane, 120 if lane == 'control' else protection_policy(profile)['business_per_minute'])
            if self.auth and kind.startswith('auth.'):
                result = self.auth.confirm(header, payload)
            elif self.switch and kind.startswith('switch.'):
                result = self.switch.dispatch(header, payload)
            elif self.switch:
                self.switch.accept_business(header)
                result = self.business(device, kind, payload, header)
            else:
                result = self.business(device, kind, payload, header)
            response = {'ok': True, 'result': result}
            if self.switch and not kind.startswith(('switch.', 'auth.')):
                receipt = self.switch.business_receipt(header)
                if receipt:
                    response['verification_receipt'] = receipt
        except (ProtocolError, KeyError, TypeError, ValueError) as error:
            from cloud.cloud_api.quota import QuotaExceeded
            response = {'ok': False, 'error': 'device_quota_exceeded' if isinstance(error, QuotaExceeded) else 'invalid_business_request'}
        extra = {'auth_session_id': header['auth_session_id']} if self.auth else {}
        return self.codec.seal('business.reply', response, epoch=header['epoch'], reply_to=header['message_id'], **extra)

    def business(self, device, kind, payload, header):
        if kind.startswith('flight.'):
            if not self.auth or not self.switch:
                raise ProtocolError('flight commands require mutual authentication')
            return self.flights.dispatch(kind, device, payload)
        if kind.startswith('mission.'):
            if not self.auth or not self.switch:
                raise ProtocolError('missions require mutual authentication and switching')
            return self.missions.dispatch(kind, device, payload, header, self.switch, self.auth)
        if kind == 'task.claim':
            result = self.store.claim(device)
        elif kind == 'task.event':
            result = self.store.event(device, payload['task_id'], payload['event_id'],
                                      payload['state'], payload.get('result'))
        elif kind == 'task.complete':
            if not self.switch:
                raise ProtocolError('switch verification is not configured')
            task = self.store.get(payload['task_id'], device)
            record = self.switch.state.get(task['request_id'])
            binding = record['binding']
            if record['state'] != 'confirmed' or not record['confirmed_receipt'] or binding['device_id'] != device or header['epoch'] != binding['epoch'] or self.switch.state.active() != binding['epoch'] or not self.switch.ready(binding['suite']):
                raise ProtocolError('switch verification is incomplete')
            policy = self.switch.state.policy(binding['epoch'])
            if self.auth and self.auth.get(header['auth_session_id'])['policy'] != policy:
                raise ProtocolError('target authentication proof missing')
            expected = explicit_combination(task['scenario'])
            if expected and (policy != expected['auth_policy'] or (expected['suite'] is not None and binding['suite'] != expected['suite'])):
                raise ProtocolError('actual combination differs from requested task target')
            evidence = {'request_id': binding['request_id'], 'epoch': binding['epoch'], 'suite': binding['suite'],
                        'auth_policy': policy, 'protection_profile': self.switch.state.profile(binding['epoch']),
                        'expires_at': self.auth.get(header['auth_session_id'])['session_expires'] if self.auth else None,
                        'identity_verified': self.auth is not None,
                        'bidirectional_probe_verified': True, 'new_session_business_verified': True,
                        'cloud_target_ready': True}
            result = self.store.verified_success(device, task['task_id'], task['request_id'], evidence)
        elif kind == 'heartbeat':
            self.store.heartbeat(device, payload)
            result = {'accepted': True}
        elif kind == 'telemetry':
            # MVP 不实现离线补传，也不把未接入的仿真数据伪装成实测。
            result = {'accepted': self.store.telemetry(device, payload, now=self.auth.clock() if self.auth else None), 'message_id': header['message_id']}
            if result['accepted'] and self.auth and self.switch and header['epoch'] == self.switch.state.active():
                mission = self.missions.active(device)
                position = payload.get('messages', {}).get('GLOBAL_POSITION_INT')
                if mission and payload.get('online') and position:
                    fields = position['fields']
                    self.missions.dispatch('mission.observe', device, {
                        'mission_id': mission['mission_id'], 'revision': mission['revision'],
                        'position': {'lat': fields['lat'] / 1e7, 'lon': fields['lon'] / 1e7},
                        'sampled_at': payload['sampled_at'] - position['age_seconds'],
                        'speed_mps': (fields['vx'] ** 2 + fields['vy'] ** 2) ** .5 / 100,
                    }, header, self.switch, self.auth)
        elif kind == 'status':
            result = self.store.device(device)
        else:
            raise ProtocolError('unsupported business operation')
        return result


class TaskRequest(BaseModel):
    device_id: str
    request_id: str
    scenario: str


class MissionRequest(BaseModel):
    device_id: str
    request_id: str
    spec: MissionSpec


def create_app(application: CloudApplication):
    api = FastAPI(title='无人机云端任务 API', version='1.0.0')
    api.add_middleware(BoundedBodyMiddleware, limit=MAX_MESSAGE * 2, error_status=400)
    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        authorization = '' if credentials is None else credentials.scheme + ' ' + credentials.credentials
        result = application.verify_login(authorization)
        if result is not True:
            raise HTTPException(status_code=503 if result is None else 401)

    @api.middleware('http')
    async def no_store(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @api.exception_handler(HTTPException)
    async def http_error(request, error):
        return Response(status_code=error.status_code)

    @api.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return Response(status_code=400)

    @api.get('/api/overview', dependencies=[Depends(authorize)], tags=['控制台'], response_model=dict)
    def overview():
        with application.lock:
            communication = None
            epoch = None
            if application.switch:
                application.switch.maintain()
                state = application.switch.state
                epoch = state.active()
                latest = state.latest()
                communication = {'epoch': epoch, 'suite': state.suite(epoch), 'auth_policy': state.policy(epoch), 'protection_profile': state.profile(epoch),
                                 'switch_state': latest['state'] if latest else 'stable'}
            mission = application.missions.active(application.codec.device_id)
            if mission and mission['last_observation'] and application.auth and application.auth.clock() >= mission['last_observation']['key_expires']:
                try:
                    application.auth.for_epoch(epoch)
                except ProtocolError:
                    mission.update(state='paused', reason='session_expired_manual_recovery_required')
                    application.missions.save(mission)
            return {'server_time': application.auth.clock() if application.auth else time.time(),
                    'device': application.store.device(application.codec.device_id),
                    'tasks': application.store.recent(application.codec.device_id),
                    'communication': communication, 'mission': mission, 'flights': application.flights.records(application.codec.device_id),
                    'recovery': application.recovery.status() if application.switch and application.auth else None,
                    'identity_auth': application.auth.status(epoch) if application.auth else {'authenticated': False, 'policy': 'disabled'}}

    @api.post('/api/recovery', dependencies=[Depends(authorize)], tags=['控制台'])
    def recover_session(body: dict):
        if not application.switch or not application.auth:
            raise HTTPException(status_code=503)
        with application.lock:
            try:
                application.quota.consume(application.codec.device_id, 'control', 120)
                return application.recovery.request(body)
            except (ProtocolError, ValueError, TypeError, KeyError):
                raise HTTPException(status_code=409)

    @api.post('/api/auth/recovery', tags=['身份认证'])
    def discover_recovery(body: dict):
        if not application.switch or not application.auth:
            raise HTTPException(status_code=503)
        with application.lock:
            try:
                application.quota.consume(application.codec.device_id, 'authentication', 30)
                return application.recovery.offer(body)
            except (ProtocolError, ValueError, TypeError, KeyError):
                raise HTTPException(status_code=400)

    def authentication(operation, body):
        if application.auth is None:
            return Response(status_code=503)
        try:
            with application.lock:
                application.quota.consume(application.codec.device_id, 'authentication', 30)
                return getattr(application.auth, operation)(body)
        except (ProtocolError, ValueError, TypeError, KeyError):
            return Response(status_code=400)
        except Exception:
            return Response(status_code=503)

    @api.post('/api/auth/start', tags=['身份认证'])
    def auth_start(body: dict):
        return authentication('start', body)

    @api.post('/api/auth/respond', tags=['身份认证'])
    def auth_respond(body: dict):
        return authentication('respond', body)

    @api.post('/api/auth/revoke', dependencies=[Depends(authorize)], tags=['身份认证'])
    def auth_revoke(body: dict):
        if application.auth is None:
            return Response(status_code=503)
        if set(body) != {'auth_session_id'} or not isinstance(body['auth_session_id'], str):
            return Response(status_code=400)
        with application.lock:
            return application.auth.revoke(body['auth_session_id'])

    @api.get('/api/tasks/{task_id}', dependencies=[Depends(authorize)], tags=['控制台'], response_model=dict)
    def task_detail(task_id: str):
        try:
            task = application.store.get(task_id, application.codec.device_id)
            return {**task, 'events': application.store.history(task_id, application.codec.device_id)}
        except KeyError:
            return Response(status_code=404)

    @api.post('/api/tasks', dependencies=[Depends(authorize)], tags=['控制台'], response_model=dict)
    def submit(body: TaskRequest):
        try:
            if body.device_id != application.codec.device_id:
                raise ProtocolError('unregistered device')
            return application.store.submit(body.device_id, body.request_id, body.scenario)
        except TaskConflict:
            return Response(status_code=409)
        except (ProtocolError, ValueError, KeyError, TypeError):
            return Response(status_code=400)

    @api.post('/api/missions', dependencies=[Depends(authorize)], tags=['航线防护'], response_model=dict)
    def submit_mission(body: MissionRequest):
        if body.device_id != application.codec.device_id:
            return Response(status_code=400)
        try:
            with application.lock:
                return application.missions.submit(body.device_id, body.request_id, body.spec.model_dump())
        except (ProtocolError, ValueError, TypeError):
            return Response(status_code=409)

    @api.get('/api/missions/{mission_id}', dependencies=[Depends(authorize)], tags=['航线防护'], response_model=dict)
    def get_mission(mission_id: str):
        try:
            with application.lock:
                return application.missions.get(mission_id, application.codec.device_id)
        except ProtocolError:
            return Response(status_code=404)

    @api.put('/api/missions/{mission_id}', dependencies=[Depends(authorize)], tags=['航线防护'], response_model=dict)
    def revise_mission(mission_id: str, body: MissionSpec):
        try:
            with application.lock:
                return application.missions.revise(mission_id, application.codec.device_id, body.model_dump())
        except (ProtocolError, ValueError, TypeError):
            return Response(status_code=400)

    @api.delete('/api/missions/{mission_id}', dependencies=[Depends(authorize)], tags=['航线防护'])
    def cancel_mission(mission_id: str):
        try:
            with application.lock:
                record = application.missions.get(mission_id, application.codec.device_id)
                record['state'] = 'cancelled'
                return application.missions.save(record)
        except ProtocolError:
            return Response(status_code=404)

    @api.post('/api/flights', dependencies=[Depends(authorize)], tags=['操作员仿真飞控'], response_model=dict)
    def submit_flight(body: dict):
        try:
            with application.lock:
                return application.flights.submit(body, application)
        except ProtocolError as error:
            return JSONResponse({'detail': str(error)}, status_code=400)
        except (ValueError, TypeError, KeyError):
            return Response(status_code=400)

    @api.post('/api/edge/exchange', tags=['云边加密通信'],
              description='提交认证加密信封；响应仍为加密信封，不接受明文业务请求。', response_model=dict)
    def exchange(body: dict):
        try:
            return JSONResponse(application.exchange(body))
        except (ProtocolError, ValueError, TypeError, KeyError):
            return Response(status_code=400)
        except Exception:
            return Response(status_code=503)

    @api.post('/api/auth/confirm', tags=['身份认证'],
              description='仅接受 v2 签名加密信封中的 auth.confirm 或 auth.activate。')
    def auth_confirm(body: dict):
        if application.auth is None:
            return Response(status_code=503)
        header = body.get('header')
        if not isinstance(header, dict) or header.get('kind') not in ('auth.confirm', 'auth.activate'):
            return Response(status_code=400)
        return exchange(body)

    document_validation_errors(api, '无效请求或未通过消息认证')
    return api


def main():
    config = json.loads(Path(os.environ['UAV_CLOUD_CONFIG']).read_text())
    # 不允许状态丢失后拿旧密钥重新从序号 1 发送。
    if not Path(config['task_state']).is_file():
        raise ValueError('task state missing: explicitly initialize or restore')
    codec = CryptoServiceCodec(device_id=config['device_id'], key_id=config['key_id'], role='cloud',
                               services=config['crypto_services'], token=Path(config['service_token_file']).read_text().strip())
    state = SwitchState(config['switch_state'], codec, config['initial_epoch'], config['initial_suite'])
    switch = CloudSwitch(state, codec.ready)
    identity = config['identity_auth']
    provider = PolicyProviders.from_config(identity)
    switch.auth_ready = provider.ready
    auth = CloudAuth(identity['state_path'], provider, codec, cloud_id=identity['cloud_id'],
                     credential_version=identity.get('credential_version', '1'), policy_for_epoch=state.policy, profile_for_epoch=state.profile)
    login_verifier = None
    if config.get('operator_token_file') and not config.get('login_verification_url'):
        # 保留既有部署的控制台操作凭据；这不能替代 UAV 的独立身份认证。
        operator_token = Path(config['operator_token_file']).read_text().strip()
        if len(operator_token) < 32:
            raise ValueError('operator credential too short')
        login_verifier = lambda received: hmac.compare_digest(received.encode(), ('Bearer ' + operator_token).encode())
    app = CloudApplication(TaskStore(config['task_state']), codec,
                           config.get('login_verification_url', 'http://127.0.0.1:8080/admin/getInfo/'), switch,
                           auth=auth, login_verifier=login_verifier)
    uvicorn.run(create_app(app), host=config.get('bind', '0.0.0.0'),
                port=int(config.get('port', 38443)), access_log=False)



if __name__ == '__main__':
    main()
