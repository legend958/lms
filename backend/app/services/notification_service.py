"""Notification fan-out, listing, and read tracking."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.notifications import NotificationList, NotificationOut
from app.core.errors import NotFoundError
from app.core.roles import CourseRole, NotificationType
from app.db.models import Notification, Task, Topic, User
from app.db.repositories import courses as courses_repo
from app.db.repositories import notifications as notifications_repo


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def serialize(notification: Notification) -> NotificationOut:
    return NotificationOut(
        id=notification.id,
        type=notification.type,
        payload=notification.payload,
        read=notification.read_at is not None,
        read_at=_as_utc(notification.read_at),
        created_at=_as_utc(notification.created_at) or datetime.now(UTC),
    )


async def fan_out_task_release(
    session: AsyncSession,
    *,
    task: Task,
    topic: Topic,
) -> int:
    """Create one notification per actively enrolled student.

    Runs inside the release transaction, so a rollback also drops the fan-out.
    """
    course = await courses_repo.get(session, task.course_id)
    student_ids = await courses_repo.list_enrolled_user_ids(
        session,
        task.course_id,
        course_role=CourseRole.STUDENT,
    )
    due_at = task.due_at
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)
    payload = {
        "task_id": str(task.id),
        "topic_id": str(topic.id),
        "course_id": str(task.course_id),
        "course_code": course.code if course is not None else None,
        "course_title": course.title if course is not None else None,
        "title": task.title,
        "week_no": task.week_no,
        "due_at": due_at.isoformat(),
    }
    for student_id in student_ids:
        await notifications_repo.create(
            session,
            user_id=student_id,
            notification_type=NotificationType.TASK_RELEASED,
            payload=payload,
        )
    return len(student_ids)


async def list_for_user(
    session: AsyncSession,
    user: User,
    *,
    unread_only: bool = False,
) -> NotificationList:
    rows = await notifications_repo.list_for_user(
        session,
        user.id,
        unread_only=unread_only,
    )
    unread_count = await notifications_repo.count_unread(session, user.id)
    return NotificationList(
        items=[serialize(row) for row in rows],
        unread_count=unread_count,
    )


async def mark_read(
    session: AsyncSession,
    *,
    user: User,
    notification_id: uuid.UUID,
) -> None:
    notification = await notifications_repo.get_for_user(session, notification_id, user.id)
    if notification is None:
        raise NotFoundError("Notification not found")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        await session.commit()
