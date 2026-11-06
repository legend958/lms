"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, request_meta
from app.api.schemas.auth import LoginRequest, LoginResponse, MembershipOut, MeOut, UserOut
from app.core.config import Settings, get_settings
from app.core.errors import TooManyRequestsError, UnauthorizedError
from app.core.ratelimit import login_limiter
from app.core.roles import Role, UserStatus
from app.core.security import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    ROLE_COOKIE,
    create_access_token,
    generate_csrf_token,
)
from app.db.models import User
from app.db.repositories import courses as courses_repo
from app.db.session import get_session
from app.services import audit_service, auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie_settings(settings: Settings) -> dict[str, object]:
    return {
        "secure": settings.cookie_secure,
        "domain": settings.cookie_domain or None,
        "samesite": "lax",
        "path": "/",
    }


def set_auth_cookies(
    response: Response,
    *,
    settings: Settings,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
    role: Role,
) -> None:
    cookies = _cookie_settings(settings)
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=settings.access_token_ttl_min * 60,
        httponly=True,
        **cookies,  # type: ignore[arg-type]
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        httponly=True,
        **cookies,  # type: ignore[arg-type]
    )
    response.set_cookie(
        ROLE_COOKIE,
        role.value,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        httponly=True,
        **cookies,  # type: ignore[arg-type]
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        httponly=False,
        **cookies,  # type: ignore[arg-type]
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, ROLE_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name,
            path="/",
            domain=settings.cookie_domain or None,
            secure=settings.cookie_secure,
            samesite="lax",
        )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    ip, request_id = request_meta(request)
    email = payload.email.lower().strip()

    allowed_ip, retry_ip = await login_limiter.allow(
        f"login:ip:{ip}", limit=settings.login_rate_limit_per_minute, window_seconds=60
    )
    allowed_account, retry_account = await login_limiter.allow(
        f"login:account:{email}",
        limit=settings.login_rate_limit_per_hour,
        window_seconds=3600,
    )
    if not allowed_ip or not allowed_account:
        retry_after = max(retry_ip, retry_account, 1)
        raise TooManyRequestsError(
            "Too many login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = await auth_service.authenticate(session, email, payload.password)
    if user is None or user.status is not UserStatus.ACTIVE:
        await audit_service.record(
            session,
            actor_id=user.id if user else None,
            action="auth.login_failed",
            entity_type="user",
            entity_id=user.id if user else None,
            after={"email": email},
            ip=ip,
            request_id=request_id,
        )
        await session.commit()
        raise UnauthorizedError("Invalid email or password")

    access_token, _ = create_access_token(user.id, user.role, settings)
    refresh_token = await auth_service.issue_refresh_token(session, user.id, settings)
    csrf_token = generate_csrf_token()
    set_auth_cookies(
        response,
        settings=settings,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
        role=user.role,
    )
    await audit_service.record(
        session,
        actor_id=user.id,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    return LoginResponse(user=UserOut.model_validate(user))


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if refresh_token:
        await auth_service.revoke_refresh_token(session, refresh_token)
    clear_auth_cookies(response, settings)
    response.status_code = 204
    return response


@router.post("/refresh", response_model=LoginResponse)
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if not raw_token:
        raise UnauthorizedError("No refresh token")
    user, new_refresh = await auth_service.rotate_refresh_token(session, raw_token, settings)
    access_token, _ = create_access_token(user.id, user.role, settings)
    csrf_token = request.cookies.get(CSRF_COOKIE) or generate_csrf_token()
    set_auth_cookies(
        response,
        settings=settings,
        access_token=access_token,
        refresh_token=new_refresh,
        csrf_token=csrf_token,
        role=user.role,
    )
    return LoginResponse(user=UserOut.model_validate(user))


@router.get("/csrf")
async def csrf(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    token = request.cookies.get(CSRF_COOKIE) or generate_csrf_token()
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=settings.refresh_token_ttl_days * 24 * 3600,
        httponly=False,
        **_cookie_settings(settings),  # type: ignore[arg-type]
    )
    return {"csrf_token": token}


@router.get("/me", response_model=MeOut)
async def me(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MeOut:
    memberships = await courses_repo.list_memberships(session, user.id)
    return MeOut(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role,
        status=user.status,
        created_at=user.created_at,
        courses=[
            MembershipOut(id=course.id, code=course.code, title=course.title, course_role=role)
            for course, role in memberships
        ],
    )
