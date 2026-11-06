"""Announcement queries, always scoped by CourseScope for non-admins."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scope import CourseScope
from app.db.models import Announcement, AnnouncementRead


async def get(session: AsyncSession, announcement_id: uuid.UUID) -> Announcement | None:
    return await session.get(Announcement, announcement_id)


async def list_visible(
    session: AsyncSession,
    scope: CourseScope,
    *,
    now: datetime,
    include_scheduled: bool = False,
    limit: int = 100,
) -> list[Announcement]:
    stmt = select(Announcement)
    if not include_scheduled:
        stmt = stmt.where(Announcement.publish_at <= now).where(
            or_(Announcement.expires_at.is_(None), Announcement.expires_at > now)
        )
    if not scope.is_admin:
        stmt = stmt.where(
            or_(Announcement.course_id.is_(None), Announcement.course_id.in_(scope.course_ids))
        )
    stmt = stmt.order_by(Announcement.publish_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars())


async def read_ids_for_user(
    session: AsyncSession, user_id: uuid.UUID, announcement_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    if not announcement_ids:
        return set()
    stmt = select(AnnouncementRead.announcement_id).where(
        and_(
            AnnouncementRead.user_id == user_id,
            AnnouncementRead.announcement_id.in_(announcement_ids),
        )
    )
    return set((await session.execute(stmt)).scalars())


async def mark_read(
    session: AsyncSession, announcement_id: uuid.UUID, user_id: uuid.UUID
) -> AnnouncementRead:
    existing = await session.get(
        AnnouncementRead, {"announcement_id": announcement_id, "user_id": user_id}
    )
    if existing is not None:
        return existing
    row = AnnouncementRead(announcement_id=announcement_id, user_id=user_id)
    session.add(row)
    return row
