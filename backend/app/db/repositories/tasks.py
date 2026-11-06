"""Student task reads and completion writes.

The student query joins ``enrollments`` and re-checks both the topic release
state and the task status. A client-supplied course id is only a narrow filter
on top of that; ``CourseScope`` is the source of truth.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import (
    CourseRole,
    CourseStatus,
    TaskProgressStatus,
    TaskStatus,
    TopicReleaseState,
)
from app.core.scope import CourseScope
from app.db.models import Course, Document, Enrollment, Task, TaskProgress, Topic

TaskFilter = Literal["all", "incomplete", "completed"]
TaskSort = Literal["due_at", "released_at"]


@dataclass(slots=True)
class StudentTaskRow:
    task: Task
    course: Course
    progress: TaskProgress | None

    @property
    def completed(self) -> bool:
        return self.progress is not None and self.progress.status is TaskProgressStatus.COMPLETED


def _student_statement(
    user_id: uuid.UUID,
    scope: CourseScope,
) -> Select[Any]:
    return (
        select(Task, Course, TaskProgress)
        .join(Topic, Topic.id == Task.topic_id)
        .join(Document, Document.id == Topic.document_id)
        .join(Course, Course.id == Task.course_id)
        .join(
            Enrollment,
            and_(
                Enrollment.course_id == Task.course_id,
                Enrollment.user_id == user_id,
                Enrollment.course_role == CourseRole.STUDENT,
            ),
        )
        .outerjoin(
            TaskProgress,
            and_(
                TaskProgress.task_id == Task.id,
                TaskProgress.student_id == user_id,
            ),
        )
        .where(
            Task.status == TaskStatus.PUBLISHED,
            Task.course_id.in_(scope.course_ids),
            Topic.release_state == TopicReleaseState.RELEASED,
            Topic.deleted_at.is_(None),
            Document.deleted_at.is_(None),
            Course.status == CourseStatus.ACTIVE,
        )
    )


async def list_student_tasks(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    scope: CourseScope,
    course_id: uuid.UUID | None = None,
    status_filter: TaskFilter = "all",
    sort: TaskSort = "due_at",
) -> list[StudentTaskRow]:
    if not scope.course_ids:
        return []

    stmt = _student_statement(user_id, scope)
    if course_id is not None:
        stmt = stmt.where(Task.course_id == course_id)
    if status_filter == "completed":
        stmt = stmt.where(TaskProgress.status == TaskProgressStatus.COMPLETED)
    elif status_filter == "incomplete":
        stmt = stmt.where(
            or_(
                TaskProgress.status.is_(None),
                TaskProgress.status == TaskProgressStatus.INCOMPLETE,
            )
        )

    if sort == "released_at":
        stmt = stmt.order_by(Topic.released_at.desc(), Task.due_at.asc(), Task.id)
    else:
        stmt = stmt.order_by(Task.due_at.asc(), Task.id)

    rows = (await session.execute(stmt)).all()
    return [
        StudentTaskRow(task=task, course=course, progress=progress)
        for task, course, progress in rows
    ]


async def get_student_task(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    scope: CourseScope,
    task_id: uuid.UUID,
) -> StudentTaskRow | None:
    """Fetch one visible task. Unreleased or out-of-scope tasks return ``None``."""
    if not scope.course_ids:
        return None
    stmt = _student_statement(user_id, scope).where(Task.id == task_id)
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    task, course, progress = row
    return StudentTaskRow(task=task, course=course, progress=progress)


async def get_progress(
    session: AsyncSession,
    *,
    task_id: uuid.UUID,
    student_id: uuid.UUID,
) -> TaskProgress | None:
    return await session.get(TaskProgress, {"task_id": task_id, "student_id": student_id})


def start_progress(
    session: AsyncSession,
    *,
    task_id: uuid.UUID,
    student_id: uuid.UUID,
    status: TaskProgressStatus,
    completed_at: datetime | None,
) -> TaskProgress:
    progress = TaskProgress(
        task_id=task_id,
        student_id=student_id,
        status=status,
        completed_at=completed_at,
    )
    session.add(progress)
    return progress
