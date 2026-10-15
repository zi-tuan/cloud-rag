"""独立身份认证容器：Ed25519/RSA/SM2 共用 HTTP 契约，私钥由本容器管理。"""
import hmac
import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import uvicorn

from shared.communication.auth_providers import provider_from_config
from shared.communication.auth_models import ProofRequest, VerifyRequest
from shared.communication.envelope import MAX_MESSAGE, ProtocolError
from shared.communication.http_api import BoundedBodyMiddleware, document_validation_errors


def create_app(provider, token):
    if len(token) < 32:
        raise ValueError('authentication service token too short')
    api = FastAPI(title='独立身份认证服务', version='1.0.0')
    api.add_middleware(BoundedBodyMiddleware, limit=MAX_MESSAGE * 2)
    bearer = HTTPBearer(auto_error=False)

    def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        received = '' if credentials is None else credentials.scheme + ' ' + credentials.credentials
        if not hmac.compare_digest(received.encode(), ('Bearer ' + token).encode()):
            raise HTTPException(status_code=401)

    @api.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return Response(status_code=400)

    @api.get('/health')
    def health():
        return {'policy': provider.policy, 'ready': True}

    @api.get('/ready', dependencies=[Depends(authorize)])
    def ready():
        try:
            valid = provider.ready()
        except (ProtocolError, ValueError, OSError):
            valid = False
        return {'policy': provider.policy, 'ready': valid}

    def dispatch(body: ProofRequest):
        try:
            if isinstance(body, VerifyRequest):
                provider.verify(body.purpose, body.transcript, body.proof, body.envelope)
                return {'verified': True}
            return {'proof': provider.prove(body.purpose, body.transcript, body.envelope)}
        except (ProtocolError, KeyError, TypeError, ValueError):
            return Response(status_code=400)

    @api.post('/v1/prove', dependencies=[Depends(authorize)])
    def prove(body: ProofRequest):
        return dispatch(body)

    @api.post('/v1/verify', dependencies=[Depends(authorize)])
    def verify(body: VerifyRequest):
        return dispatch(body)

    document_validation_errors(api, '无效认证请求或未通过消息认证')
    return api


def main():
    config = json.loads(Path(os.environ.get('UAV_AUTH_CONFIG', '/config/auth.json')).read_text())
    provider = provider_from_config(config)
    token = Path(config['token_file']).read_text().strip()
    uvicorn.run(create_app(provider, token), host=config.get('bind', '0.0.0.0'),
                port=int(config.get('port', 8080)), access_log=False)


if __name__ == '__main__':
    main()
