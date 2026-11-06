"""Role capability flags used by services and the admin permission matrix.

Rows live in ``permissions`` and are seeded by migration 0010. A missing row
falls back to the conservative default below, which keeps a fresh test database
behaving exactly like a freshly migrated one.

Admins bypass every capability check: the matrix controls student and teacher
portals, never the people who administer them.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.roles import Role, UserStatus
from app.db.models import Permission, User
from app.services import audit_service

logger = structlog.get_logger(__name__)

SELF_ENROLL = "student.self_enroll"
TEACHER_CREATE_COURSE = "teacher.create_course"
ASSISTANT_ENABLED = "student.assistant_enabled"
QUIZ_AUTO_APPROVE = "quiz.auto_approve"

CAPABILITY_ROLES: dict[str, Role] = {
    SELF_ENROLL: Role.STUDENT,
    TEACHER_CREATE_COURSE: Role.TEACHER,
    ASSISTANT_ENABLED: Role.STUDENT,
    QUIZ_AUTO_APPROVE: Role.TEACHER,
}

DEFAULT_ENABLED: dict[str, bool] = {
    SELF_ENROLL: False,
    TEACHER_CREATE_COURSE: False,
    ASSISTANT_ENABLED: True,
    QUIZ_AUTO_APPROVE: True,
}

CACHE_TTL_SECONDS = 30


class PermissionService:
    """Small read-through cache in front of the permissions table."""

    def __init__(self, ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._cache: dict[tuple[Role, str], tuple[float, bool]] = {}

    def reset(self) -> None:
        self._cache.clear()

    def invalidate(self, *, role: Role, key: str) -> None:
        self._cache.pop((role, key), None)

    async def can(self, session: AsyncSession, role: Role, key: str) -> bool:
        if role is Role.ADMIN:
            return True
        if key not in CAPABILITY_ROLES:
            return False

        cache_key = (role, key)
        cached = self._cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and cached[0] > now:
            return cached[1]

        row = await session.get(Permission, {"key": key, "role": role})
        value = row.enabled if row is not None else DEFAULT_ENABLED.get(key, False)
        self._cache[cache_key] = (now + self.ttl_seconds, value)
        return value

    async def list_permissions(self, session: AsyncSession) -> list[dict[str, Any]]:
        rows = list((await session.execute(select(Permission))).scalars())
        stored = {(row.key, row.role): row.enabled for row in rows}

        counts = {
            role: int(count)
            for role, count in (
                await session.execute(
                    select(User.role, func.count(User.id))
                    .where(User.status == UserStatus.ACTIVE)
                    .group_by(User.role)
                )
            ).all()
        }

        result: list[dict[str, Any]] = []
        for key, role in CAPABILITY_ROLES.items():
            result.append(
                {
                    "key": key,
                    "role": role,
                    "enabled": stored.get((key, role), DEFAULT_ENABLED[key]),
                    "affected_users": counts.get(role, 0),
                }
            )
        return result

    async def set_permission(
        self,
        session: AsyncSession,
        *,
        actor: User,
        key: str,
        role: Role,
        enabled: bool,
        ip: str | None = None,
        request_id: str | None = None,
    ) -> Permission:
        expected_role = CAPABILITY_ROLES.get(key)
        if expected_role is None:
            raise ValidationError("Unknown capability key")
        if role is not expected_role:
            raise ValidationError("That capability does not apply to this role")

        permission = await session.get(Permission, {"key": key, "role": role})
        before: dict[str, Any] = {
            "key": key,
            "role": role.value,
            "enabled": (
                permission.enabled if permission is not None else DEFAULT_ENABLED.get(key, False)
            ),
        }
        if permission is None:
            permission = Permission(key=key, role=role, enabled=enabled)
            session.add(permission)
        else:
            permission.enabled = enabled

        await audit_service.record(
            session,
            actor_id=actor.id,
            action="permission.update",
            entity_type="permission",
            entity_id=f"{key}:{role.value}",
            before=before,
            after={"key": key, "role": role.value, "enabled": enabled},
            ip=ip,
            request_id=request_id,
        )
        await session.commit()
        self.invalidate(role=role, key=key)
        logger.info("permission_updated", key=key, role=role.value, enabled=enabled)
        return permission


_service: PermissionService | None = None


def get_permission_service() -> PermissionService:
    global _service
    if _service is None:
        _service = PermissionService()
    return _service


def reset_permission_cache() -> None:
    """Test helper: drop cached values between isolated test cases."""
    get_permission_service().reset()


__all__ = [
    "ASSISTANT_ENABLED",
    "CAPABILITY_ROLES",
    "DEFAULT_ENABLED",
    "QUIZ_AUTO_APPROVE",
    "SELF_ENROLL",
    "TEACHER_CREATE_COURSE",
    "PermissionService",
    "get_permission_service",
    "reset_permission_cache",
]
