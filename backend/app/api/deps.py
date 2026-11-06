"""FastAPI dependencies: auth, roles, course scope, request metadata."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.roles import CourseRole, Role, UserStatus
from app.core.scope import CourseScope
from app.core.security import ACCESS_COOKIE, decode_access_token
from app.db.models import User
from app.db.repositories import courses as courses_repo
from app.db.repositories import users as users_repo
from app.db.session import get_session

LAST_ACTIVE_THROTTLE = timedelta(minutes=5)


def _extract_token(request: Request) -> str | None:
    token = request.cookies.get(ACCESS_COOKIE)
    if token:
        return token
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value
    return None


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    token = _extract_token(request)
    if not token:
        raise UnauthorizedError("Authentication required")
    try:
        claims = decode_access_token(token, settings)
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Invalid or expired session") from exc

    user = await users_repo.get(session, claims.user_id)
    if user is None or user.status is not UserStatus.ACTIVE:
        raise UnauthorizedError("Account is not active")

    now = datetime.now(UTC)
    last_active = user.last_active_at
    if last_active is not None and last_active.tzinfo is None:
        last_active = last_active.replace(tzinfo=UTC)
    if last_active is None or now - last_active > LAST_ACTIVE_THROTTLE:
        user.last_active_at = now
        await session.commit()

    return user


def require_role(*roles: Role):  # type: ignore[no-untyped-def]
    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise ForbiddenError("You do not have permission to perform this action")
        return user

    return dependency


async def get_course_scope(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CourseScope:
    if user.role is Role.ADMIN:
        course_ids = await courses_repo.list_all_ids(session)
    elif user.role is Role.TEACHER:
        taught = await courses_repo.list_course_ids_for_user(
            session, user.id, course_role=CourseRole.TEACHER
        )
        created = await courses_repo.list_created_ids(session, user.id)
        course_ids = taught | created
    else:
        course_ids = await courses_repo.list_course_ids_for_user(session, user.id)
    return CourseScope(user_id=user.id, role=user.role, course_ids=frozenset(course_ids))


def request_meta(request: Request) -> tuple[str | None, str]:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip: str | None = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else None
    request_id = getattr(request.state, "request_id", "unknown")
    return ip, request_id
