"""Notification list and read endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.schemas.notifications import NotificationList
from app.db.models import User
from app.db.session import get_session
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationList)
async def list_notifications(
    unread: bool = Query(default=False),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> NotificationList:
    return await notification_service.list_for_user(session, user, unread_only=unread)


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notification_read(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await notification_service.mark_read(
        session,
        user=user,
        notification_id=notification_id,
    )
