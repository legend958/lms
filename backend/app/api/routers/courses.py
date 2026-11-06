"""Course and enrollment endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_course_scope, get_current_user, request_meta, require_role
from app.api.schemas.courses import (
    CourseCreate,
    CourseOut,
    CourseUpdate,
    EnrollmentCreate,
    EnrollmentOut,
    EnrollmentResult,
)
from app.core.errors import NotFoundError
from app.core.roles import Role
from app.core.scope import CourseScope
from app.db.models import Enrollment, User
from app.db.repositories import courses as courses_repo
from app.db.session import get_session
from app.services import course_service

router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("", response_model=list[CourseOut])
async def list_courses(
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> list[CourseOut]:
    rows = await courses_repo.list_for_scope(session, scope)
    return [CourseOut.model_validate(row) for row in rows]


@router.get("/{course_id}", response_model=CourseOut)
async def get_course(
    course_id: uuid.UUID,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> CourseOut:
    if not scope.is_admin:
        scope.require(course_id)
    course = await courses_repo.get(session, course_id)
    if course is None:
        raise NotFoundError("Course not found")
    return CourseOut.model_validate(course)


@router.post("", response_model=CourseOut, status_code=201)
async def create_course(
    payload: CourseCreate,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN, Role.TEACHER)),
    session: AsyncSession = Depends(get_session),
) -> CourseOut:
    ip, request_id = request_meta(request)
    course = await course_service.create_course(
        session,
        actor=actor,
        code=payload.code,
        title=payload.title,
        term=payload.term,
        description=payload.description,
        ip=ip,
        request_id=request_id,
    )
    return CourseOut.model_validate(course)


@router.post("/{course_id}/self-enroll", response_model=EnrollmentResult)
async def self_enroll(
    course_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role(Role.STUDENT)),
    session: AsyncSession = Depends(get_session),
) -> EnrollmentResult:
    ip, request_id = request_meta(request)
    added = await course_service.self_enroll(
        session,
        actor=actor,
        course_id=course_id,
        ip=ip,
        request_id=request_id,
    )
    return EnrollmentResult(added=added)


@router.patch("/{course_id}", response_model=CourseOut)
async def update_course(
    course_id: uuid.UUID,
    payload: CourseUpdate,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> CourseOut:
    ip, request_id = request_meta(request)
    course = await course_service.update_course(
        session,
        actor=actor,
        course_id=course_id,
        changes=payload.model_dump(exclude_unset=True),
        ip=ip,
        request_id=request_id,
    )
    return CourseOut.model_validate(course)


@router.delete("/{course_id}", status_code=204)
async def archive_course(
    course_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> None:
    ip, request_id = request_meta(request)
    await course_service.archive_course(
        session, actor=actor, course_id=course_id, ip=ip, request_id=request_id
    )


@router.get("/{course_id}/enrollments", response_model=list[EnrollmentOut])
async def list_enrollments(
    course_id: uuid.UUID,
    user: User = Depends(get_current_user),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> list[EnrollmentOut]:
    if not scope.is_admin:
        scope.require(course_id)
    rows = await session.execute(
        select(User, Enrollment.course_role)
        .join(Enrollment, Enrollment.user_id == User.id)
        .where(Enrollment.course_id == course_id)
        .order_by(User.name)
    )
    return [
        EnrollmentOut(
            user_id=member.id,
            name=member.name,
            email=member.email,
            course_role=role,
        )
        for member, role in rows.all()
    ]


@router.post("/{course_id}/enrollments", response_model=EnrollmentResult)
async def add_enrollments(
    course_id: uuid.UUID,
    payload: EnrollmentCreate,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> EnrollmentResult:
    ip, request_id = request_meta(request)
    added = await course_service.add_enrollments(
        session,
        actor=actor,
        course_id=course_id,
        user_ids=payload.user_ids,
        course_role=payload.course_role,
        ip=ip,
        request_id=request_id,
    )
    return EnrollmentResult(added=added)


@router.delete("/{course_id}/enrollments/{user_id}", status_code=204)
async def remove_enrollment(
    course_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> None:
    ip, request_id = request_meta(request)
    await course_service.remove_enrollment(
        session,
        actor=actor,
        course_id=course_id,
        user_id=user_id,
        ip=ip,
        request_id=request_id,
    )
