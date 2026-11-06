"""Course and enrollment queries."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import CourseRole, CourseStatus, UserStatus
from app.core.scope import CourseScope
from app.db.models import Course, Enrollment, User


async def get(session: AsyncSession, course_id: uuid.UUID) -> Course | None:
    return await session.get(Course, course_id)


async def get_by_code(session: AsyncSession, code: str) -> Course | None:
    result = await session.execute(select(Course).where(Course.code == code.upper().strip()))
    return result.scalar_one_or_none()


async def list_for_scope(session: AsyncSession, scope: CourseScope) -> list[Course]:
    stmt = select(Course)
    if not scope.is_admin:
        stmt = stmt.where(Course.id.in_(scope.course_ids))
    stmt = stmt.order_by(Course.code)
    return list((await session.execute(stmt)).scalars())


async def list_all_ids(session: AsyncSession) -> set[uuid.UUID]:
    rows = await session.execute(select(Course.id).where(Course.status == CourseStatus.ACTIVE))
    return set(rows.scalars())


async def list_created_ids(session: AsyncSession, user_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await session.execute(
        select(Course.id).where(Course.created_by == user_id, Course.status == CourseStatus.ACTIVE)
    )
    return set(rows.scalars())


async def list_memberships(
    session: AsyncSession, user_id: uuid.UUID
) -> list[tuple[Course, CourseRole]]:
    stmt = (
        select(Course, Enrollment.course_role)
        .join(Enrollment, Enrollment.course_id == Course.id)
        .where(Enrollment.user_id == user_id, Course.status == CourseStatus.ACTIVE)
        .order_by(Course.code)
    )
    return [(course, role) for course, role in (await session.execute(stmt)).all()]


async def list_course_ids_for_user(
    session: AsyncSession, user_id: uuid.UUID, *, course_role: CourseRole | None = None
) -> set[uuid.UUID]:
    stmt = (
        select(Enrollment.course_id)
        .join(Course, Course.id == Enrollment.course_id)
        .where(Enrollment.user_id == user_id, Course.status == CourseStatus.ACTIVE)
    )
    if course_role is not None:
        stmt = stmt.where(Enrollment.course_role == course_role)
    return set((await session.execute(stmt)).scalars())


async def list_enrollments(session: AsyncSession, course_id: uuid.UUID) -> list[Enrollment]:
    stmt = (
        select(Enrollment).where(Enrollment.course_id == course_id).order_by(Enrollment.created_at)
    )
    return list((await session.execute(stmt)).scalars())


async def list_enrolled_user_ids(
    session: AsyncSession,
    course_id: uuid.UUID,
    *,
    course_role: CourseRole | None = None,
) -> list[uuid.UUID]:
    """Active users enrolled in a course, used for notification fan-out."""
    stmt = (
        select(Enrollment.user_id)
        .join(User, User.id == Enrollment.user_id)
        .where(
            Enrollment.course_id == course_id,
            User.status == UserStatus.ACTIVE,
        )
    )
    if course_role is not None:
        stmt = stmt.where(Enrollment.course_role == course_role)
    return list((await session.execute(stmt)).scalars())


async def get_enrollment(
    session: AsyncSession, course_id: uuid.UUID, user_id: uuid.UUID
) -> Enrollment | None:
    return await session.get(Enrollment, {"course_id": course_id, "user_id": user_id})


async def enroll(
    session: AsyncSession,
    course_id: uuid.UUID,
    user_id: uuid.UUID,
    course_role: CourseRole = CourseRole.STUDENT,
) -> Enrollment:
    enrollment = Enrollment(course_id=course_id, user_id=user_id, course_role=course_role)
    session.add(enrollment)
    return enrollment


async def unenroll(session: AsyncSession, enrollment: Enrollment) -> None:
    await session.delete(enrollment)
