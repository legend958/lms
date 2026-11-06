"""Announcement authoring, visibility, and read tracking."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.announcements import AnnouncementOut
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.roles import Role
from app.core.scope import CourseScope
from app.db.models import Announcement, Course, User
from app.db.repositories import announcements as announcements_repo
from app.services import audit_service


def _snapshot(announcement: Announcement) -> dict[str, Any]:
    return {
        "title": announcement.title,
        "body": announcement.body,
        "course_id": str(announcement.course_id) if announcement.course_id else None,
        "publish_at": announcement.publish_at.isoformat(),
        "expires_at": announcement.expires_at.isoformat() if announcement.expires_at else None,
    }


def _ensure_can_write(user: User, scope: CourseScope, course_id: uuid.UUID | None) -> None:
    if user.role is Role.ADMIN:
        return
    if user.role is not Role.TEACHER:
        raise ForbiddenError("Only teachers and admins can publish announcements")
    if course_id is None:
        raise ForbiddenError("Only admins can publish institution-wide announcements")
    scope.require(course_id)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def serialize(
    session: AsyncSession,
    announcements: list[Announcement],
    user_id: uuid.UUID,
) -> list[AnnouncementOut]:
    """Shared announcement projection, including per-user read state."""
    if not announcements:
        return []

    course_ids = {a.course_id for a in announcements if a.course_id is not None}
    author_ids = {a.author_id for a in announcements}
    course_titles: dict[uuid.UUID, str] = {}
    author_names: dict[uuid.UUID, str] = {}
    if course_ids:
        rows = await session.execute(
            select(Course.id, Course.title).where(Course.id.in_(course_ids))
        )
        course_titles = {cid: title for cid, title in rows.all()}
    rows = await session.execute(select(User.id, User.name).where(User.id.in_(author_ids)))
    author_names = {uid: name for uid, name in rows.all()}

    read_ids = await announcements_repo.read_ids_for_user(
        session, user_id, [a.id for a in announcements]
    )
    return [
        AnnouncementOut(
            id=a.id,
            title=a.title,
            body=a.body,
            course_id=a.course_id,
            course_title=course_titles.get(a.course_id) if a.course_id else None,
            author_id=a.author_id,
            author_name=author_names.get(a.author_id, "Unknown"),
            publish_at=a.publish_at,
            expires_at=a.expires_at,
            read=a.id in read_ids,
            created_at=a.created_at,
        )
        for a in announcements
    ]


async def create(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    title: str,
    body: str,
    course_id: uuid.UUID | None,
    publish_at: datetime | None,
    expires_at: datetime | None,
    ip: str | None = None,
    request_id: str | None = None,
) -> Announcement:
    _ensure_can_write(actor, scope, course_id)
    publish_at = _as_utc(publish_at)
    expires_at = _as_utc(expires_at)
    if publish_at and expires_at and expires_at <= publish_at:
        raise ValidationError("expires_at must be after publish_at")

    announcement = Announcement(
        course_id=course_id,
        author_id=actor.id,
        title=title.strip(),
        body=body,
        publish_at=publish_at or datetime.now(UTC),
        expires_at=expires_at,
    )
    session.add(announcement)
    await session.flush()
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="announcement.create",
        entity_type="announcement",
        entity_id=announcement.id,
        after=_snapshot(announcement),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    return announcement


async def update(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    announcement_id: uuid.UUID,
    changes: dict[str, Any],
    ip: str | None = None,
    request_id: str | None = None,
) -> Announcement:
    announcement = await announcements_repo.get(session, announcement_id)
    if announcement is None:
        raise NotFoundError("Announcement not found")
    if actor.role is not Role.ADMIN:
        if announcement.author_id != actor.id:
            raise ForbiddenError("You can only edit your own announcements")
        if announcement.course_id is not None:
            scope.require(announcement.course_id)

    before = _snapshot(announcement)
    for field in ("title", "body", "publish_at", "expires_at"):
        if field in changes and changes[field] is not None:
            value = _as_utc(changes[field]) if "at" in field else changes[field]
            setattr(announcement, field, value)
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="announcement.update",
        entity_type="announcement",
        entity_id=announcement.id,
        before=before,
        after=_snapshot(announcement),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    return announcement


async def delete(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    announcement_id: uuid.UUID,
    ip: str | None = None,
    request_id: str | None = None,
) -> None:
    announcement = await announcements_repo.get(session, announcement_id)
    if announcement is None:
        raise NotFoundError("Announcement not found")
    if actor.role is not Role.ADMIN:
        if announcement.author_id != actor.id:
            raise ForbiddenError("You can only delete your own announcements")
        if announcement.course_id is not None:
            scope.require(announcement.course_id)

    before = _snapshot(announcement)
    await session.delete(announcement)
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="announcement.delete",
        entity_type="announcement",
        entity_id=announcement.id,
        before=before,
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
