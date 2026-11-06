"""Announcement schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    course_id: uuid.UUID | None = None
    publish_at: datetime | None = None
    expires_at: datetime | None = None


class AnnouncementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1)
    publish_at: datetime | None = None
    expires_at: datetime | None = None


class AnnouncementOut(BaseModel):
    id: uuid.UUID
    title: str
    body: str
    course_id: uuid.UUID | None
    course_title: str | None
    author_id: uuid.UUID
    author_name: str
    publish_at: datetime
    expires_at: datetime | None
    read: bool
    created_at: datetime
