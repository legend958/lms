"""Teacher analytics endpoints, scoped to courses the teacher owns."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_course_scope, require_role
from app.api.schemas.analytics import (
    AnalyticsStudentDetail,
    AnalyticsStudentList,
    AnalyticsTopics,
    CohortKpis,
)
from app.core.roles import Role
from app.core.scope import CourseScope
from app.db.session import get_session
from app.services import analytics_service
from app.services.analytics_service import DEFAULT_LIMIT, MAX_LIMIT, StudentSort

router = APIRouter(
    prefix="/teacher/analytics",
    tags=["teacher-analytics"],
    dependencies=[Depends(require_role(Role.TEACHER, Role.ADMIN))],
)


@router.get("", response_model=CohortKpis)
async def get_cohort_kpis(
    course_id: uuid.UUID = Query(...),
    week: int | None = Query(default=None, ge=1, le=200),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> CohortKpis:
    return await analytics_service.cohort_kpis(session, scope=scope, course_id=course_id, week=week)


@router.get("/students", response_model=AnalyticsStudentList)
async def list_students(
    course_id: uuid.UUID = Query(...),
    week: int | None = Query(default=None, ge=1, le=200),
    sort: StudentSort = Query(default="completion"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> AnalyticsStudentList:
    return await analytics_service.student_rows(
        session,
        scope=scope,
        course_id=course_id,
        week=week,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )


@router.get("/topics", response_model=AnalyticsTopics)
async def get_topic_analytics(
    course_id: uuid.UUID = Query(...),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> AnalyticsTopics:
    return await analytics_service.topics(session, scope=scope, course_id=course_id)


@router.get("/students/{student_id}", response_model=AnalyticsStudentDetail)
async def get_student_analytics(
    student_id: uuid.UUID,
    course_id: uuid.UUID = Query(...),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> AnalyticsStudentDetail:
    return await analytics_service.student_detail(
        session,
        scope=scope,
        course_id=course_id,
        student_id=student_id,
    )
