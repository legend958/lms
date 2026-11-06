"""User lifecycle management (admin-facing)."""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.roles import CourseRole, Role, UserStatus
from app.core.security import hash_password
from app.db.models import User
from app.db.repositories import courses as courses_repo
from app.db.repositories import users as users_repo
from app.services import audit_service


def _snapshot(user: User) -> dict[str, Any]:
    return {
        "name": user.name,
        "email": user.email,
        "role": user.role.value,
        "status": user.status.value,
    }


async def create_user(
    session: AsyncSession,
    *,
    actor: User,
    name: str,
    email: str,
    role: Role,
    course_ids: list[uuid.UUID] | None = None,
    password: str | None = None,
    ip: str | None = None,
    request_id: str | None = None,
) -> tuple[User, str | None]:
    normalized = email.lower().strip()
    if await users_repo.get_by_email(session, normalized) is not None:
        raise ConflictError("A user with that email already exists")

    temporary_password: str | None = None
    if password:
        raw_password = password
    else:
        temporary_password = secrets.token_urlsafe(9)
        raw_password = temporary_password

    user = User(
        name=name.strip(),
        email=normalized,
        password_hash=hash_password(raw_password),
        role=role,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()

    for course_id in course_ids or []:
        course = await courses_repo.get(session, course_id)
        if course is None:
            raise NotFoundError(f"Course {course_id} not found")
        course_role = CourseRole.TEACHER if role is Role.TEACHER else CourseRole.STUDENT
        await courses_repo.enroll(session, course_id, user.id, course_role)

    await audit_service.record(
        session,
        actor_id=actor.id,
        action="user.create",
        entity_type="user",
        entity_id=user.id,
        after=_snapshot(user),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    return user, temporary_password


async def update_user(
    session: AsyncSession,
    *,
    actor: User,
    user_id: uuid.UUID,
    changes: dict[str, Any],
    ip: str | None = None,
    request_id: str | None = None,
) -> User:
    user = await users_repo.get(session, user_id)
    if user is None or user.status is UserStatus.DELETED:
        raise NotFoundError("User not found")

    new_status = changes.get("status")
    if actor.id == user.id and new_status is not None and new_status is not UserStatus.ACTIVE:
        raise ForbiddenError("You cannot deactivate your own account")
    if (
        actor.id == user.id
        and changes.get("role") is not None
        and changes["role"] is not Role.ADMIN
    ):
        raise ForbiddenError("You cannot remove your own admin role")

    before = _snapshot(user)
    for field in ("name", "role", "status"):
        if field in changes and changes[field] is not None:
            setattr(user, field, changes[field])
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="user.update",
        entity_type="user",
        entity_id=user.id,
        before=before,
        after=_snapshot(user),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    return user


async def delete_user(
    session: AsyncSession,
    *,
    actor: User,
    user_id: uuid.UUID,
    ip: str | None = None,
    request_id: str | None = None,
) -> None:
    user = await users_repo.get(session, user_id)
    if user is None or user.status is UserStatus.DELETED:
        raise NotFoundError("User not found")
    if actor.id == user.id:
        raise ForbiddenError("You cannot delete your own account")

    before = _snapshot(user)
    user.status = UserStatus.DELETED
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="user.delete",
        entity_type="user",
        entity_id=user.id,
        before=before,
        after=_snapshot(user),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
