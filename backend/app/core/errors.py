"""Domain errors.

Services and repositories raise these; the API layer maps them to RFC 7807
responses. Keeping them independent of FastAPI preserves the layering.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    status_code = 400
    error_type = "https://lms/errors/domain"
    title = "Request failed"

    def __init__(
        self,
        detail: str,
        *,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.headers = headers or {}
        self.extra = extra or {}


class UnauthorizedError(DomainError):
    status_code = 401
    error_type = "https://lms/errors/unauthorized"
    title = "Authentication required"


class ForbiddenError(DomainError):
    status_code = 403
    error_type = "https://lms/errors/forbidden"
    title = "Not allowed"


class NotFoundError(DomainError):
    status_code = 404
    error_type = "https://lms/errors/not-found"
    title = "Not found"


class ConflictError(DomainError):
    status_code = 409
    error_type = "https://lms/errors/conflict"
    title = "Conflict"


class ValidationError(DomainError):
    status_code = 422
    error_type = "https://lms/errors/validation"
    title = "Validation failed"


class TooManyRequestsError(DomainError):
    status_code = 429
    error_type = "https://lms/errors/rate-limited"
    title = "Too many requests"


class ServiceUnavailableError(DomainError):
    status_code = 503
    error_type = "https://lms/errors/service-unavailable"
    title = "Service temporarily unavailable"
