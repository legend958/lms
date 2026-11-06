"""Seed documents, topics, and chunks without running the embedding pipeline."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.core.roles import DocumentStatus, TaskStatus, TopicReleaseState, TopicSource
from app.db.models import Chunk, Document, Task, Topic
from tests.fakes import FakeVectorStore


@dataclass
class SeededTopic:
    topic: Topic
    chunks: list[Chunk] = field(default_factory=list)


@dataclass
class SeededDocument:
    document: Document
    topics: list[SeededTopic] = field(default_factory=list)

    def named(self, title: str) -> SeededTopic:
        return next(item for item in self.topics if item.topic.title == title)


async def seed_document(
    session: AsyncSession,
    world,
    *,
    status: DocumentStatus = DocumentStatus.READY,
    topic_specs: list[dict[str, object]] | None = None,
    course_id: uuid.UUID | None = None,
    uploaded_by: uuid.UUID | None = None,
) -> SeededDocument:
    """Create a document with sequential pages and deterministic vector ids."""
    document_id = uuid7()
    document = Document(
        id=document_id,
        course_id=course_id or world.course_a.id,
        uploaded_by=uploaded_by or world.teacher.id,
        filename=f"{document_id}.pdf",
        checksum=hashlib.sha256(str(document_id).encode()).hexdigest(),
        size_bytes=1024,
        page_count=40,
        status=status,
        pipeline_version=1,
        topic_count=0,
        topics_source=TopicSource.LLM,
    )
    session.add(document)
    await session.flush()

    specs = topic_specs or []
    seeded = SeededDocument(document=document)
    chunk_index = 0
    page_cursor = 1
    for order_index, spec in enumerate(specs):
        title = str(spec["title"])
        chunk_count = int(spec.get("chunk_count", 2))  # type: ignore[arg-type]
        released = bool(spec.get("released", False))
        page_start = int(spec.get("page_start", page_cursor))  # type: ignore[arg-type]
        page_end = page_start + chunk_count - 1
        page_cursor = page_end + 1
        topic = Topic(
            document_id=document.id,
            course_id=document.course_id,
            title=title,
            summary=f"{title} summary",
            page_start=page_start,
            page_end=page_end,
            order_index=order_index,
            release_state=(TopicReleaseState.RELEASED if released else TopicReleaseState.DRAFT),
            released_by=world.teacher.id if released else None,
            released_at=datetime.now(UTC) if released else None,
            needs_review=False,
        )
        session.add(topic)
        await session.flush()

        item = SeededTopic(topic=topic)
        for offset in range(chunk_count):
            chunk = Chunk(
                vector_id=f"{document.id}:{chunk_index}",
                document_id=document.id,
                course_id=document.course_id,
                topic_id=topic.id,
                chunk_index=chunk_index,
                text=f"{title} chunk {offset}",
                page_start=page_start + offset,
                page_end=page_start + offset,
                section_title=title,
                token_count=12,
                released=released,
            )
            session.add(chunk)
            item.chunks.append(chunk)
            chunk_index += 1
        seeded.topics.append(item)

    document.topic_count = len(seeded.topics)
    await session.commit()
    return seeded


async def seed_published_task(
    session: AsyncSession,
    topic: Topic,
    *,
    week_no: int = 1,
    due_at: datetime | None = None,
    status: TaskStatus = TaskStatus.PUBLISHED,
) -> Task:
    """Create a task row directly, bypassing the teacher release endpoint."""
    task = Task(
        topic_id=topic.id,
        course_id=topic.course_id,
        title=topic.title,
        description=topic.summary,
        week_no=week_no,
        due_at=due_at or datetime(2026, 12, 10, 23, 59, tzinfo=UTC),
        status=status,
    )
    session.add(task)
    await session.commit()
    return task


def fake_vector_store(seeded: SeededDocument) -> FakeVectorStore:
    store = FakeVectorStore()
    for item in seeded.topics:
        for chunk in item.chunks:
            store.records[chunk.vector_id] = {
                "embedding": [0.0, 0.0],
                "document": chunk.text,
                "metadata": {
                    "course_id": str(chunk.course_id),
                    "document_id": str(chunk.document_id),
                    "topic_id": str(chunk.topic_id),
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "section_title": chunk.section_title or "",
                    "released": chunk.released,
                    "pipeline_version": 1,
                    "chunk_index": chunk.chunk_index,
                },
            }
    return store
