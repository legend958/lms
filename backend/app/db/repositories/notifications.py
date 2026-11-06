"""Notification queries. Notifications are always private to one user."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import NotificationType
from app.db.models import Notification


async def create(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    notification_type: NotificationType,
    payload: dict[str, Any],
) -> Notification:
    notification = Notification(user_id=user_id, type=notification_type, payload=payload)
    session.add(notification)
    return notification


async def list_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    unread_only: bool = False,
    limit: int = 50,
) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc(), Notification.id).limit(limit)
    return list((await session.execute(stmt)).scalars())


async def count_unread(session: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = select(func.count(Notification.id)).where(
        Notification.user_id == user_id,
        Notification.read_at.is_(None),
    )
    return int((await session.execute(stmt)).scalar_one())


async def get_for_user(
    session: AsyncSession,
    notification_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Notification | None:
    notification = await session.get(Notification, notification_id)
    if notification is None or notification.user_id != user_id:
        return None
    return notification
