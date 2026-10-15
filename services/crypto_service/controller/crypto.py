"""FastAPI 路由、请求校验、服务鉴权与响应转换。"""
from __future__ import annotations

import hmac
import logging

from fastapi import FastAPI, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from shared.communication.envelope import MAX_MESSAGE, ProtocolError
from shared.communication.http_api import BoundedBodyMiddleware, document_validation_errors
from services.crypto_service.service.crypto import CryptoService

LOG = logging.getLogger('crypto-service')


class SealRequest(BaseModel):
    kind: str
    payload: dict
    epoch: str
    reply_to: str | None = None
    auth_session_id: str | None = None


class OpenRequest(BaseModel):
    envelope: dict


class SessionRequest(BaseModel):
    epoch: str


class LeaseRequest(SessionRequest):
    protection_profile: str
    expires_at: int


class RetirementRequest(SessionRequest):
    model_config = {'extra': 'forbid'}
    retire_at: float


def create_app(service: CryptoService, token: str):
    token = token.strip()
    if len(token) < 32:
        raise ValueError('service token is too short')
    api = FastAPI(title='认证加密服务', version=service.version)
    api.add_middleware(BoundedBodyMiddleware, limit=MAX_MESSAGE * 2)
    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        received = '' if credentials is None else credentials.scheme + ' ' + credentials.credentials
        if not hmac.compare_digest(received.encode(), ('Bearer ' + token).encode()):
            raise HTTPException(status_code=401)

    @api.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse({'error': 'unauthorized'}, status_code=error.status_code)

    @api.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse({'error': 'invalid or unauthenticated message'}, status_code=400)

    @api.get('/', include_in_schema=False)
    @api.get('/health', tags=['健康检查'])
    def health():
        return {'suite': service.suite, 'version': service.version, 'ready': True}

    @api.get('/ready', tags=['健康检查'])
    def ready():
        return JSONResponse({'suite': service.suite, 'version': service.version,
                             'ready': not service.broken}, status_code=503 if service.broken else 200)

    def respond(operation, callback):
        try:
            return JSONResponse(callback())
        except (ProtocolError, ValueError, KeyError, TypeError):
            return JSONResponse({'error': 'invalid or unauthenticated message'}, status_code=400)
        except Exception:
            LOG.error('operation failed', extra={'operation': operation})
            return JSONResponse({'error': 'service unavailable'}, status_code=503)

    @api.post('/v1/seal', dependencies=[Depends(authorize)], tags=['加密消息'])
    def seal(body: SealRequest):
        return respond('/v1/seal', lambda: service.seal(**body.model_dump(exclude_none=True)))

    @api.post('/v1/open', dependencies=[Depends(authorize)], tags=['加密消息'])
    def open_message(body: OpenRequest):
        def response():
            header, payload = service.open(body.envelope)
            return {'header': header, 'payload': payload}
        return respond('/v1/open', response)

    @api.post('/v1/sessions/prepare', dependencies=[Depends(authorize)], tags=['会话'])
    def prepare(body: SessionRequest):
        def response():
            service.prepare(body.epoch)
            return {'epoch': body.epoch, 'suite': service.suite, 'prepared': True}
        return respond('/v1/sessions/prepare', response)

    @api.post('/v1/sessions/retire', dependencies=[Depends(authorize)], tags=['会话'])
    def retire(body: SessionRequest):
        def response():
            service.retire(body.epoch)
            return {'retired': True}
        return respond('/v1/sessions/retire', response)

    @api.post('/v1/sessions/lease', dependencies=[Depends(authorize)], tags=['会话'])
    def lease(body: LeaseRequest):
        def response():
            service.lease(body.epoch, body.protection_profile, body.expires_at)
            return {'leased': True, **body.model_dump()}
        return respond('/v1/sessions/lease', response)

    @api.post('/v1/sessions/retirement', dependencies=[Depends(authorize)], tags=['会话'])
    def retirement(body: RetirementRequest):
        def response():
            service.schedule_retirement(body.epoch, body.retire_at)
            return {'scheduled': True, 'epoch': body.epoch, 'retire_at': body.retire_at, 'suite': service.suite}
        return respond('/v1/sessions/retirement', response)

    document_validation_errors(api, '无效请求或未通过消息认证')
    return api
