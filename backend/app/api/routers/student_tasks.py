"""Student dashboard, task list, and completion endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_course_scope, require_role
from app.api.schemas.tasks import (
    StudentDashboardOut,
    StudentTaskList,
    StudentTaskOut,
    StudentTaskUpdate,
    StudentTaskUpdateResult,
    TaskFilter,
    TaskSort,
)
from app.core.roles import Role
from app.core.scope import CourseScope
from app.db.session import get_session
from app.services import task_service

router = APIRouter(
    prefix="/student",
    tags=["student-tasks"],
    dependencies=[Depends(require_role(Role.STUDENT))],
)


@router.get("/dashboard", response_model=StudentDashboardOut)
async def get_student_dashboard(
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> StudentDashboardOut:
    return await task_service.student_dashboard(session, scope=scope)


@router.get("/tasks", response_model=StudentTaskList)
async def list_student_tasks(
    status: TaskFilter = Query(default="all"),
    course_id: uuid.UUID | None = Query(default=None),
    sort: TaskSort = Query(default="due_at"),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> StudentTaskList:
    items: list[StudentTaskOut] = await task_service.list_student_tasks(
        session,
        scope=scope,
        course_id=course_id,
        status_filter=status,
        sort=sort,
    )
    return StudentTaskList(items=items, next_cursor=None)


@router.patch("/tasks/{task_id}", response_model=StudentTaskUpdateResult)
async def update_student_task(
    task_id: uuid.UUID,
    payload: StudentTaskUpdate,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> StudentTaskUpdateResult:
    return await task_service.set_student_task_status(
        session,
        scope=scope,
        task_id=task_id,
        status=payload.status,
    )
