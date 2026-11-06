"""Document upload, listing, and ingestion status schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.core.roles import DocumentStatus, TopicSource


class DocumentOut(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    course_code: str
    course_title: str
    uploaded_by: uuid.UUID
    uploader_name: str
    filename: str
    checksum: str
    size_bytes: int
    page_count: int | None
    status: DocumentStatus
    error_code: str | None
    error_detail: str | None
    pipeline_version: int
    topic_count: int
    topics_source: TopicSource | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class DocumentUploadOut(BaseModel):
    document_id: uuid.UUID
    status: DocumentStatus


class DocumentStatusOut(BaseModel):
    document_id: uuid.UUID
    status: DocumentStatus
    stage: str
    progress_pct: int
    attempts: int
    error_code: str | None
    error_detail: str | None
    started_at: datetime | None
    finished_at: datetime | None
    chunk_count: int
