"""Release-gated query helpers and study-assistant logging."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import DocumentStatus, TopicReleaseState
from app.core.scope import CourseScope
from app.db.models import Chunk, Document, RagQueryLog, Topic


@dataclass(frozen=True)
class VisibleChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    course_id: uuid.UUID
    topic_id: uuid.UUID
    title: str
    text: str
    page: int
    section_title: str | None


async def resolve_visible_chunks(
    session: AsyncSession,
    *,
    vector_ids: list[str],
    scope: CourseScope,
    course_id: uuid.UUID | None = None,
    topic_id: uuid.UUID | None = None,
) -> dict[str, VisibleChunk]:
    """Map Chroma ids back to chunks that still satisfy the release gate."""
    if not vector_ids:
        return {}

    course_ids = {course_id} if course_id is not None else scope.course_ids
    stmt = (
        select(Chunk, Document.filename)
        .join(Document, Document.id == Chunk.document_id)
        .join(Topic, Topic.id == Chunk.topic_id)
        .where(
            Chunk.vector_id.in_(vector_ids),
            Chunk.released.is_(True),
            Chunk.course_id.in_(course_ids),
            Document.deleted_at.is_(None),
            Document.status == DocumentStatus.READY,
            Topic.deleted_at.is_(None),
            Topic.release_state == TopicReleaseState.RELEASED,
            Topic.course_id.in_(course_ids),
        )
    )
    if topic_id is not None:
        stmt = stmt.where(Chunk.topic_id == topic_id)

    result: dict[str, VisibleChunk] = {}
    for chunk, filename in (await session.execute(stmt)).all():
        result[chunk.vector_id] = VisibleChunk(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            course_id=chunk.course_id,
            topic_id=chunk.topic_id,  # type: ignore[arg-type]
            title=filename,
            text=chunk.text,
            page=chunk.page_start,
            section_title=chunk.section_title,
        )
    return result


async def get_released_topic(
    session: AsyncSession,
    *,
    scope: CourseScope,
    topic_id: uuid.UUID,
) -> Topic | None:
    stmt = (
        select(Topic)
        .join(Document, Document.id == Topic.document_id)
        .where(
            Topic.id == topic_id,
            Topic.deleted_at.is_(None),
            Topic.release_state == TopicReleaseState.RELEASED,
            Topic.course_id.in_(scope.course_ids),
            Document.deleted_at.is_(None),
            Document.status == DocumentStatus.READY,
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_topic_context(
    session: AsyncSession,
    *,
    topic_id: uuid.UUID,
) -> tuple[Topic, Document] | None:
    """Fetch a topic and its document regardless of release state.

    Callers use this to distinguish "not found / out of scope" (404) from
    "exists in scope but is not released" (refusal).
    """
    stmt = (
        select(Topic, Document)
        .join(Document, Document.id == Topic.document_id)
        .where(
            Topic.id == topic_id,
            Topic.deleted_at.is_(None),
            Document.deleted_at.is_(None),
        )
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return row[0], row[1]


async def log_query(session: AsyncSession, **values: object) -> None:
    session.add(RagQueryLog(**values))
    await session.commit()
