"""Topic, task, and topic-scoped chunk queries."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import TopicReleaseState
from app.core.scope import CourseScope
from app.db.models import Chunk, Document, Task, Topic


async def get(
    session: AsyncSession,
    topic_id: uuid.UUID,
    *,
    include_deleted: bool = False,
) -> Topic | None:
    topic = await session.get(Topic, topic_id)
    if topic is None:
        return None
    if topic.deleted_at is not None and not include_deleted:
        return None
    return topic


async def get_for_update(session: AsyncSession, topic_id: uuid.UUID) -> Topic | None:
    stmt = select(Topic).where(Topic.id == topic_id, Topic.deleted_at.is_(None)).with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_for_document(
    session: AsyncSession,
    document_id: uuid.UUID,
) -> list[Topic]:
    stmt = (
        select(Topic)
        .where(Topic.document_id == document_id, Topic.deleted_at.is_(None))
        .order_by(Topic.order_index, Topic.page_start, Topic.id)
    )
    return list((await session.execute(stmt)).scalars())


async def list_all_active(session: AsyncSession) -> list[Topic]:
    stmt = (
        select(Topic)
        .where(Topic.deleted_at.is_(None))
        .order_by(Topic.course_id, Topic.document_id, Topic.order_index)
    )
    return list((await session.execute(stmt)).scalars())


async def list_for_course(session: AsyncSession, course_id: uuid.UUID) -> list[Topic]:
    stmt = (
        select(Topic)
        .where(Topic.course_id == course_id, Topic.deleted_at.is_(None))
        .order_by(Topic.order_index, Topic.page_start, Topic.id)
    )
    return list((await session.execute(stmt)).scalars())


async def list_released_for_scope(
    session: AsyncSession,
    scope: CourseScope,
    *,
    course_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> list[Topic]:
    """Student-facing topic query. Never remove these filters."""
    stmt = (
        select(Topic)
        .join(Document, Document.id == Topic.document_id)
        .where(
            Topic.deleted_at.is_(None),
            Document.deleted_at.is_(None),
            Topic.release_state == TopicReleaseState.RELEASED,
            Topic.course_id.in_(scope.course_ids),
        )
    )
    if course_id is not None:
        stmt = stmt.where(Topic.course_id == course_id)
    if document_id is not None:
        stmt = stmt.where(Topic.document_id == document_id)
    stmt = stmt.order_by(Topic.order_index, Topic.page_start, Topic.id)
    return list((await session.execute(stmt)).scalars())


async def list_released_chunks_for_scope(
    session: AsyncSession,
    scope: CourseScope,
    *,
    topic_id: uuid.UUID | None = None,
) -> list[Chunk]:
    """Student-facing chunk query with both the SQL and release gates."""
    stmt = (
        select(Chunk)
        .join(Topic, Topic.id == Chunk.topic_id)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.released.is_(True),
            Chunk.course_id.in_(scope.course_ids),
            Topic.release_state == TopicReleaseState.RELEASED,
            Topic.deleted_at.is_(None),
            Document.deleted_at.is_(None),
        )
        .order_by(Chunk.document_id, Chunk.chunk_index)
    )
    if topic_id is not None:
        stmt = stmt.where(Chunk.topic_id == topic_id)
    return list((await session.execute(stmt)).scalars())


async def chunk_counts(
    session: AsyncSession,
    topic_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not topic_ids:
        return {}
    stmt = (
        select(Chunk.topic_id, func.count(Chunk.id))
        .where(Chunk.topic_id.in_(topic_ids))
        .group_by(Chunk.topic_id)
    )
    return {topic_id: count for topic_id, count in (await session.execute(stmt)).all()}


async def tasks_for_topics(
    session: AsyncSession,
    topic_ids: list[uuid.UUID],
) -> dict[uuid.UUID, Task]:
    if not topic_ids:
        return {}
    stmt = select(Task).where(Task.topic_id.in_(topic_ids))
    return {task.topic_id: task for task in (await session.execute(stmt)).scalars()}


async def reassign_chunks(
    session: AsyncSession,
    from_topic_ids: list[uuid.UUID],
    to_topic_id: uuid.UUID,
) -> None:
    if not from_topic_ids:
        return
    await session.execute(
        update(Chunk).where(Chunk.topic_id.in_(from_topic_ids)).values(topic_id=to_topic_id)
    )


async def renumber(session: AsyncSession, document_id: uuid.UUID) -> None:
    topics = await list_for_document(session, document_id)
    for index, topic in enumerate(topics):
        topic.order_index = index


async def count_active_for_document(session: AsyncSession, document_id: uuid.UUID) -> int:
    stmt = select(func.count(Topic.id)).where(
        Topic.document_id == document_id,
        Topic.deleted_at.is_(None),
    )
    return int((await session.execute(stmt)).scalar_one())


async def count_released_for_document(session: AsyncSession, document_id: uuid.UUID) -> int:
    stmt = select(func.count(Topic.id)).where(
        Topic.document_id == document_id,
        Topic.deleted_at.is_(None),
        Topic.release_state == TopicReleaseState.RELEASED,
    )
    return int((await session.execute(stmt)).scalar_one())


async def delete_for_document(session: AsyncSession, document_id: uuid.UUID) -> None:
    """Remove generated topics before a document is re-ingested.

    Callers must first reject documents with released topics.
    """
    topics = await list_for_document(session, document_id)
    for topic in topics:
        await session.delete(topic)


async def next_order_index(session: AsyncSession, document_id: uuid.UUID) -> int:
    stmt = select(func.max(Topic.order_index)).where(
        Topic.document_id == document_id,
        Topic.deleted_at.is_(None),
    )
    value = (await session.execute(stmt)).scalar_one_or_none()
    return int(value) + 1 if value is not None else 0


async def shift_orders(session: AsyncSession, document_id: uuid.UUID, from_index: int) -> None:
    await session.execute(
        update(Topic)
        .where(
            Topic.document_id == document_id,
            Topic.deleted_at.is_(None),
            Topic.order_index >= from_index,
        )
        .values(order_index=Topic.order_index + 1)
    )


async def list_chunks(session: AsyncSession, topic_id: uuid.UUID) -> list[Chunk]:
    stmt = select(Chunk).where(Chunk.topic_id == topic_id).order_by(Chunk.chunk_index, Chunk.id)
    return list((await session.execute(stmt)).scalars())


async def list_chunks_for_topics(
    session: AsyncSession,
    topic_ids: list[uuid.UUID],
) -> list[Chunk]:
    if not topic_ids:
        return []
    stmt = select(Chunk).where(Chunk.topic_id.in_(topic_ids)).order_by(Chunk.chunk_index, Chunk.id)
    return list((await session.execute(stmt)).scalars())


async def chunks_by_ids(
    session: AsyncSession,
    chunk_ids: list[uuid.UUID],
) -> list[Chunk]:
    if not chunk_ids:
        return []
    stmt = select(Chunk).where(Chunk.id.in_(chunk_ids)).order_by(Chunk.chunk_index, Chunk.id)
    return list((await session.execute(stmt)).scalars())


async def set_chunk_topic(
    session: AsyncSession,
    chunk_ids: list[uuid.UUID],
    topic_id: uuid.UUID,
) -> None:
    if not chunk_ids:
        return
    await session.execute(update(Chunk).where(Chunk.id.in_(chunk_ids)).values(topic_id=topic_id))


async def set_topic_released(
    session: AsyncSession,
    topic_id: uuid.UUID,
    *,
    released: bool,
) -> None:
    await session.execute(update(Chunk).where(Chunk.topic_id == topic_id).values(released=released))


async def get_task_for_topic(session: AsyncSession, topic_id: uuid.UUID) -> Task | None:
    stmt = select(Task).where(Task.topic_id == topic_id)
    return (await session.execute(stmt)).scalar_one_or_none()
