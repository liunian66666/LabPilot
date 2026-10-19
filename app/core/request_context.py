"""请求追踪与认证入口 IP 限流，不信任客户端转发头。"""

from contextvars import ContextVar
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException
from starlette.responses import JSONResponse

from app.core.admission import auth_rate
from app.core.config import get_settings

request_id: ContextVar[str] = ContextVar("request_id", default="")


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = uuid4().hex
        token = request_id.set(rid)
        started = monotonic()
        status = 500

        async def traced_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", rid.encode()),
                ]
            await send(message)

        try:
            settings = get_settings()
            if scope.get("path") in {
                settings.API_V1_PREFIX + "/auth/login",
                settings.API_V1_PREFIX + "/auth/register",
            }:
                client = scope.get("client")
                try:
                    auth_rate.check(
                        client[0] if client else "unknown",
                        settings.AUTH_REQUESTS_PER_MINUTE,
                    )
                except HTTPException as exc:
                    return await JSONResponse(
                        {"detail": exc.detail},
                        status_code=exc.status_code,
                        headers=exc.headers,
                    )(scope, receive, traced_send)
            await self.app(scope, receive, traced_send)
        finally:
            from app.core.logging import log_event

            log_event(
                "http_request",
                status=status,
                method=scope.get("method"),
                duration_seconds=round(monotonic() - started, 3),
            )
            request_id.reset(token)
