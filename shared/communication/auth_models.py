"""独立认证接口的请求对象；客户端、HTTP 校验及接口文档使用同一定义。"""
from pydantic import BaseModel, ConfigDict


class ProofRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    purpose: str
    transcript: dict
    envelope: dict | None


class VerifyRequest(ProofRequest):
    proof: str
