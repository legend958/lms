"""Task creation for released topics, plus student reads and completion.

Teacher-side writes keep a published task's copy aligned with its topic.
Student-side reads always go through the enrollment + release-gate query in
``db.repositories.tasks``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.tasks import (
    StudentDashboardOut,
    StudentTaskCounts,
    StudentTaskOut,
    StudentTaskUpdateResult,
)
from app.core.errors import NotFoundError
from app.core.roles import TaskProgressStatus, TaskStatus
from app.core.scope import CourseScope
from app.db.models import Task, Topic
from app.db.repositories import announcements as announcements_repo
from app.db.repositories import tasks as tasks_repo
from app.services import analytics_service, announcement_service


async def get_for_topic(session: AsyncSession, topic_id: uuid.UUID) -> Task | None:
    stmt = select(Task).where(Task.topic_id == topic_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def upsert_published(
    session: AsyncSession,
    topic: Topic,
    *,
    week_no: int,
    due_at: datetime,
) -> Task:
    """Create a published task, or republish an archived task on re-release."""
    task = await get_for_topic(session, topic.id)
    if task is None:
        task = Task(
            topic_id=topic.id,
            course_id=topic.course_id,
            title=topic.title,
            description=topic.summary,
            week_no=week_no,
            due_at=due_at,
            status=TaskStatus.PUBLISHED,
        )
        session.add(task)
        return task

    task.course_id = topic.course_id
    task.title = topic.title
    task.description = topic.summary
    task.week_no = week_no
    task.due_at = due_at
    task.status = TaskStatus.PUBLISHED
    return task


async def archive_for_topic(session: AsyncSession, topic: Topic) -> Task | None:
    task = await get_for_topic(session, topic.id)
    if task is not None:
        task.status = TaskStatus.ARCHIVED
    return task


def sync_published_copy(task: Task | None, topic: Topic) -> None:
    """Keep a published task's copy aligned with topic edits."""
    if task is None or task.status is not TaskStatus.PUBLISHED:
        return
    task.title = topic.title
    task.description = topic.summary


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def serialize_student_task(row: tasks_repo.StudentTaskRow, *, now: datetime) -> StudentTaskOut:
    due_at = _as_utc(row.task.due_at) or now
    completed = row.completed
    progress = row.progress
    return StudentTaskOut(
        id=row.task.id,
        topic_id=row.task.topic_id,
        title=row.task.title,
        description=row.task.description,
        course={
            "id": row.course.id,
            "code": row.course.code,
            "title": row.course.title,
        },
        week_no=row.task.week_no,
        due_at=due_at,
        status=(progress.status if progress is not None else TaskProgressStatus.INCOMPLETE),
        overdue=(not completed and due_at < now),
        completed_at=_as_utc(progress.completed_at) if progress is not None else None,
    )


async def list_student_tasks(
    session: AsyncSession,
    *,
    scope: CourseScope,
    course_id: uuid.UUID | None = None,
    status_filter: tasks_repo.TaskFilter = "all",
    sort: tasks_repo.TaskSort = "due_at",
) -> list[StudentTaskOut]:
    if course_id is not None:
        # Never trust the client filter: a foreign course id is a 404, not an
        # empty list that would confirm the course exists.
        scope.require(course_id)
    rows = await tasks_repo.list_student_tasks(
        session,
        user_id=scope.user_id,
        scope=scope,
        course_id=course_id,
        status_filter=status_filter,
        sort=sort,
    )
    now = datetime.now(UTC)
    return [serialize_student_task(row, now=now) for row in rows]


async def student_dashboard(
    session: AsyncSession,
    *,
    scope: CourseScope,
) -> StudentDashboardOut:
    rows = await tasks_repo.list_student_tasks(
        session,
        user_id=scope.user_id,
        scope=scope,
        sort="due_at",
    )
    now = datetime.now(UTC)
    items = [serialize_student_task(row, now=now) for row in rows]
    completed = sum(1 for item in items if item.status is TaskProgressStatus.COMPLETED)
    overdue = sum(1 for item in items if item.overdue)
    pending = sum(1 for item in items if item.status is TaskProgressStatus.INCOMPLETE)

    announcements = await announcements_repo.list_visible(
        session,
        scope,
        now=now,
        limit=3,
    )
    return StudentDashboardOut(
        counts=StudentTaskCounts(pending=pending, completed=completed, overdue=overdue),
        due_soon=[item for item in items if item.status is TaskProgressStatus.INCOMPLETE][:3],
        announcements=await announcement_service.serialize(session, announcements, scope.user_id),
    )


async def set_student_task_status(
    session: AsyncSession,
    *,
    scope: CourseScope,
    task_id: uuid.UUID,
    status: TaskProgressStatus,
) -> StudentTaskUpdateResult:
    """Idempotent completion. Progress rows are never deleted."""
    row = await tasks_repo.get_student_task(
        session,
        user_id=scope.user_id,
        scope=scope,
        task_id=task_id,
    )
    if row is None:
        raise NotFoundError("Task not found")

    now = datetime.now(UTC)
    progress = row.progress
    if progress is None:
        progress = tasks_repo.start_progress(
            session,
            task_id=task_id,
            student_id=scope.user_id,
            status=status,
            completed_at=now if status is TaskProgressStatus.COMPLETED else None,
        )
    elif progress.status is not status:
        progress.status = status
        progress.completed_at = now if status is TaskProgressStatus.COMPLETED else None
    # Same status is a no-op, so the original completed_at is preserved.

    await session.commit()
    await analytics_service.invalidate_course(row.task.course_id)
    return StudentTaskUpdateResult(
        status=progress.status,
        completed_at=_as_utc(progress.completed_at),
    )
