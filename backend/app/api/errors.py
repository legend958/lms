"""RFC 7807 problem-detail responses."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import DomainError
from app.core.logging import get_logger

logger = get_logger(__name__)


def problem(
    *,
    status: int,
    title: str,
    detail: str,
    error_type: str,
    request_id: str,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": error_type,
        "title": title,
        "status": status,
        "detail": detail,
        "request_id": request_id,
    }
    if extra:
        body.update(extra)
    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
        headers=headers,
    )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return problem(
            status=exc.status_code,
            title=exc.title,
            detail=exc.detail,
            error_type=exc.error_type,
            request_id=_request_id(request),
            headers=exc.headers,
            extra=exc.extra or None,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {"loc": list(err.get("loc", [])), "msg": err.get("msg", "invalid")}
            for err in exc.errors()
        ]
        return problem(
            status=422,
            title="Validation failed",
            detail="One or more fields are invalid.",
            error_type="https://lms/errors/validation",
            request_id=_request_id(request),
            extra={"errors": errors},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", path=request.url.path, request_id=_request_id(request))
        return problem(
            status=500,
            title="Internal server error",
            detail="An unexpected error occurred.",
            error_type="https://lms/errors/internal",
            request_id=_request_id(request),
        )
