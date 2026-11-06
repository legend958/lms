"""Schemas for admin oversight endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.roles import DocumentStatus, Role, TopicSource


class AuditLogOut(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_name: str | None
    action: str
    entity_type: str
    entity_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    ip: str | None
    request_id: str | None
    created_at: datetime


class ContentRow(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    course_code: str
    course_title: str
    uploaded_by: uuid.UUID
    uploader_name: str
    filename: str
    size_bytes: int
    page_count: int | None
    status: DocumentStatus
    error_code: str | None
    topic_count: int
    released_topics: int
    chunk_count: int
    topics_source: TopicSource | None
    created_at: datetime
    updated_at: datetime


class ContentActionOut(BaseModel):
    document_id: uuid.UUID
    status: DocumentStatus


class ContentDownloadOut(BaseModel):
    document_id: uuid.UUID
    filename: str
    url: str
    expires_in: int


class PermissionOut(BaseModel):
    key: str
    role: Role
    enabled: bool
    affected_users: int


class PermissionUpdate(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    role: Role
    enabled: bool


class ImportRowResult(BaseModel):
    row_number: int
    email: str | None = None
    status: Literal["created", "skipped", "error"]
    reason: str | None = None
    user_id: uuid.UUID | None = None
    temporary_password: str | None = None


class ImportReport(BaseModel):
    mode: Literal["atomic", "partial"]
    total: int
    created: int
    skipped: int
    failed: int
    rows: list[ImportRowResult]


class SystemHealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, str]
    queue_depth: int | None
    last_ingestion_failure: dict[str, Any] | None
    generated_at: datetime
