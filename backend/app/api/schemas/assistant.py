"""Study-assistant request and SSE payload DTOs."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    course_id: uuid.UUID | None = None
    topic_id: uuid.UUID | None = None


class CitationOut(BaseModel):
    document_id: uuid.UUID
    title: str
    page: int


class CitationsEvent(BaseModel):
    citations: list[CitationOut]
    refusal: bool
