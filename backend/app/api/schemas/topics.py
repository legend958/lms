"""Topic review, release, and student-view schemas.

The student DTOs are deliberately separate from the teacher DTOs. They have no
``release_state``, no audit fields, and no vector metadata, so a future route
cannot accidentally serialize the gate state or internal identifiers.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.roles import TaskStatus, TopicReleaseState


class TopicOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    course_id: uuid.UUID
    title: str
    summary: str
    page_start: int
    page_end: int
    order_index: int
    chunk_count: int
    release_state: TopicReleaseState
    released_at: datetime | None
    needs_review: bool
    task_id: uuid.UUID | None
    task_status: TaskStatus | None
    created_at: datetime
    updated_at: datetime


class TopicUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=90)
    summary: str | None = Field(default=None, max_length=2000)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    order_index: int | None = Field(default=None, ge=0)


class TopicMerge(BaseModel):
    topic_ids: list[uuid.UUID] = Field(min_length=2, max_length=50)
    title: str = Field(min_length=3, max_length=90)


class TopicSplit(BaseModel):
    at_chunk_id: uuid.UUID
    new_title: str = Field(min_length=3, max_length=90)


class TopicReleaseRequest(BaseModel):
    week_no: int = Field(ge=1, le=52)
    due_at: datetime
    generate_quiz: bool = True


class TopicReleaseResult(BaseModel):
    topic_id: uuid.UUID
    task_id: uuid.UUID
    task_status: TaskStatus
    released_at: datetime
    chroma_synced: bool
    reconcile_queued: bool
    quiz_id: uuid.UUID | None = None
    quiz_queued: bool = False


class TopicUnreleaseResult(BaseModel):
    topic_id: uuid.UUID
    release_state: TopicReleaseState
    chroma_synced: bool


class StudentTopicView(BaseModel):
    """Student-safe topic projection. Do not add release or audit fields."""

    id: uuid.UUID
    course_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    summary: str
    page_start: int
    page_end: int
    chunk_count: int


class StudentChunkView(BaseModel):
    """Student-safe chunk projection without vector metadata or gate flags."""

    id: uuid.UUID
    text: str
    page_start: int
    page_end: int
    section_title: str | None


class StudentTopicDetail(BaseModel):
    topic: StudentTopicView
    chunks: list[StudentChunkView]
