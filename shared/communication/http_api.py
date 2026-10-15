"""HTTP 请求限额：在 FastAPI 解析 JSON 前限制大小和读取时间。"""
import asyncio

from fastapi.responses import Response


def document_validation_errors(api, description):
    """接口使用统一的 400 校验应答，OpenAPI 同步描述实际状态码。"""
    original_openapi = api.openapi

    def openapi():
        schema = original_openapi()
        for path in schema['paths'].values():
            for operation in path.values():
                responses = operation.get('responses', {})
                if '422' in responses:
                    responses.pop('422')
                    responses['400'] = {'description': description}
        return schema

    api.openapi = openapi


class BoundedBodyMiddleware:
    def __init__(self, app, limit, error_status=413):
        self.app, self.limit, self.error_status = app, limit, error_status

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        try:
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    data = message.get('body', b'')
                    size += len(data)
                    if size > self.limit:
                        return await Response(status_code=self.error_status)(scope, receive, send)
                    chunks.append(data)
                    if not message.get('more_body', False):
                        break
        except TimeoutError:
            return await Response(status_code=408)(scope, receive, send)
        body = b''.join(chunks)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()

        await self.app(scope, replay, send)
