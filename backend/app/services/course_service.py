"""Course lifecycle and enrollment management."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.roles import CourseRole, CourseStatus, Role
from app.core.scope import CourseScope
from app.db.models import Course, User
from app.db.repositories import courses as courses_repo
from app.db.repositories import users as users_repo
from app.services import audit_service, permission_service


def _snapshot(course: Course) -> dict[str, Any]:
    return {
        "code": course.code,
        "title": course.title,
        "term": course.term,
        "description": course.description,
        "status": course.status.value,
    }


async def create_course(
    session: AsyncSession,
    *,
    actor: User,
    code: str,
    title: str,
    term: str | None,
    description: str | None,
    ip: str | None = None,
    request_id: str | None = None,
) -> Course:
    if actor.role is Role.TEACHER and not await permission_service.get_permission_service().can(
        session, Role.TEACHER, permission_service.TEACHER_CREATE_COURSE
    ):
        raise ForbiddenError("Teachers are not allowed to create courses")
    if actor.role not in {Role.ADMIN, Role.TEACHER}:
        raise ForbiddenError("You do not have permission to create courses")

    normalized_code = code.upper().strip()
    if await courses_repo.get_by_code(session, normalized_code) is not None:
        raise ConflictError("A course with that code already exists")
    course = Course(
        code=normalized_code,
        title=title.strip(),
        term=term,
        description=description,
        created_by=actor.id,
        status=CourseStatus.ACTIVE,
    )
    session.add(course)
    await session.flush()
    if actor.role is Role.TEACHER:
        await courses_repo.enroll(session, course.id, actor.id, CourseRole.TEACHER)
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="course.create",
        entity_type="course",
        entity_id=course.id,
        after=_snapshot(course),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    return course


async def update_course(
    session: AsyncSession,
    *,
    actor: User,
    course_id: uuid.UUID,
    changes: dict[str, Any],
    ip: str | None = None,
    request_id: str | None = None,
) -> Course:
    course = await courses_repo.get(session, course_id)
    if course is None:
        raise NotFoundError("Course not found")
    before = _snapshot(course)
    for field in ("title", "term", "description", "status"):
        if field in changes and changes[field] is not None:
            setattr(course, field, changes[field])
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="course.update",
        entity_type="course",
        entity_id=course.id,
        before=before,
        after=_snapshot(course),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    return course


async def archive_course(
    session: AsyncSession,
    *,
    actor: User,
    course_id: uuid.UUID,
    ip: str | None = None,
    request_id: str | None = None,
) -> None:
    course = await courses_repo.get(session, course_id)
    if course is None:
        raise NotFoundError("Course not found")
    before = _snapshot(course)
    course.status = CourseStatus.ARCHIVED
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="course.archive",
        entity_type="course",
        entity_id=course.id,
        before=before,
        after=_snapshot(course),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()


async def add_enrollments(
    session: AsyncSession,
    *,
    actor: User,
    course_id: uuid.UUID,
    user_ids: list[uuid.UUID],
    course_role: CourseRole,
    ip: str | None = None,
    request_id: str | None = None,
) -> int:
    course = await courses_repo.get(session, course_id)
    if course is None:
        raise NotFoundError("Course not found")

    added = 0
    for user_id in user_ids:
        user = await users_repo.get(session, user_id)
        if user is None:
            raise NotFoundError(f"User {user_id} not found")
        existing = await courses_repo.get_enrollment(session, course_id, user_id)
        if existing is not None:
            continue
        await courses_repo.enroll(session, course_id, user_id, course_role)
        added += 1

    await audit_service.record(
        session,
        actor_id=actor.id,
        action="enrollment.create",
        entity_type="course",
        entity_id=course.id,
        after={"user_ids": [str(uid) for uid in user_ids], "course_role": course_role.value},
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    return added


async def remove_enrollment(
    session: AsyncSession,
    *,
    actor: User,
    course_id: uuid.UUID,
    user_id: uuid.UUID,
    ip: str | None = None,
    request_id: str | None = None,
) -> None:
    enrollment = await courses_repo.get_enrollment(session, course_id, user_id)
    if enrollment is None:
        raise NotFoundError("Enrollment not found")
    await courses_repo.unenroll(session, enrollment)
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="enrollment.delete",
        entity_type="course",
        entity_id=course_id,
        before={"user_id": str(user_id)},
        ip=ip,
        request_id=request_id,
    )
    await session.commit()


async def self_enroll(
    session: AsyncSession,
    *,
    actor: User,
    course_id: uuid.UUID,
    ip: str | None = None,
    request_id: str | None = None,
) -> int:
    """Enroll the current student when the capability is enabled.

    Returns 1 when a new enrollment was created and 0 when the student was
    already enrolled. Admin bypass in ``PermissionService`` does not matter
    here because the route is student-only.
    """
    allowed = await permission_service.get_permission_service().can(
        session, Role.STUDENT, permission_service.SELF_ENROLL
    )
    if not allowed:
        raise ForbiddenError("Student self-enrollment is disabled")

    course = await courses_repo.get(session, course_id)
    if course is None or course.status is not CourseStatus.ACTIVE:
        raise NotFoundError("Course not found")

    existing = await courses_repo.get_enrollment(session, course_id, actor.id)
    if existing is not None:
        return 0

    await courses_repo.enroll(session, course_id, actor.id, CourseRole.STUDENT)
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="enrollment.self_create",
        entity_type="course",
        entity_id=course.id,
        after={"user_id": str(actor.id), "course_role": CourseRole.STUDENT.value},
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    return 1


def can_manage_course(user: User, scope: CourseScope, course_id: uuid.UUID) -> bool:
    if user.role is Role.ADMIN:
        return True
    return scope.includes(course_id)
