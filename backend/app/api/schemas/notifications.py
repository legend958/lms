"""Notification DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.core.roles import NotificationType


class NotificationOut(BaseModel):
    id: uuid.UUID
    type: NotificationType
    payload: dict[str, Any]
    read: bool
    read_at: datetime | None
    created_at: datetime


class NotificationList(BaseModel):
    items: list[NotificationOut]
    unread_count: int
