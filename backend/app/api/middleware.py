"""Request context (id, timing, access log) and CSRF protection."""

from __future__ import annotations

import time
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.security import CSRF_COOKIE, constant_time_equals

logger = get_logger("http")

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_EXEMPT_PATHS = frozenset({"/api/v1/auth/login", "/api/v1/auth/csrf"})


class RequestContextMiddleware:
    """Attach a request id, log the request, and return the id in a header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = ""
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                request_id = value.decode("latin-1")
                break
        if not request_id:
            request_id = uuid.uuid4().hex

        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "request",
                request_id=request_id,
                method=scope.get("method"),
                path=scope.get("path"),
                status=status_code,
                duration_ms=duration_ms,
            )


class CSRFMiddleware:
    """Double-submit CSRF check for unsafe methods.

    The token is issued as a readable cookie at login; the client echoes it in
    the X-CSRF-Token header. Login itself is exempt because no session exists
    yet.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = request.url.path
        if request.method in UNSAFE_METHODS and path not in CSRF_EXEMPT_PATHS:
            cookie = request.cookies.get(CSRF_COOKIE)
            header = request.headers.get("x-csrf-token")
            if not cookie or not header or not constant_time_equals(cookie, header):
                request_id = getattr(request.state, "request_id", "unknown")
                response = JSONResponse(
                    status_code=403,
                    media_type="application/problem+json",
                    content={
                        "type": "https://lms/errors/csrf",
                        "title": "CSRF validation failed",
                        "status": 403,
                        "detail": "Missing or invalid CSRF token.",
                        "request_id": request_id,
                    },
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
