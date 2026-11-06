"""Topic review, release, and the release gate.

Authorization is always checked against the teacher's ``CourseScope``. Release
commits Postgres first and then updates Chroma, because a lagging vector index
can only hide content. Unrelease does the opposite: Chroma is hidden first so a
failed Postgres write can never leave released vectors visible.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.topics import (
    StudentChunkView,
    StudentTopicDetail,
    StudentTopicView,
    TopicMerge,
    TopicOut,
    TopicReleaseRequest,
    TopicReleaseResult,
    TopicSplit,
    TopicUnreleaseResult,
    TopicUpdate,
)
from app.core.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from app.core.roles import DocumentStatus, TopicReleaseState
from app.core.scope import CourseScope
from app.db.models import Chunk, Document, Topic, User
from app.db.repositories import documents as documents_repo
from app.db.repositories import quizzes as quizzes_repo
from app.db.repositories import topics as topics_repo
from app.rag.vector_store import VectorStore, VectorStoreError
from app.services import (
    analytics_service,
    audit_service,
    notification_service,
    quiz_service,
    task_service,
)
from app.workers.queue import enqueue_quiz_generation, enqueue_reconcile

logger = structlog.get_logger(__name__)


def assert_topic_released(topic: Topic) -> None:
    """Independent service-layer half of the release gate."""
    if topic.deleted_at is not None or topic.release_state is not TopicReleaseState.RELEASED:
        raise NotFoundError("Topic not found")


def _snapshot(topic: Topic) -> dict[str, Any]:
    return {
        "document_id": str(topic.document_id),
        "title": topic.title,
        "summary": topic.summary,
        "page_start": topic.page_start,
        "page_end": topic.page_end,
        "order_index": topic.order_index,
        "release_state": topic.release_state.value,
        "released_at": topic.released_at.isoformat() if topic.released_at else None,
        "needs_review": topic.needs_review,
    }


def _clean_title(value: str) -> str:
    title = " ".join(value.split())
    if len(title) < 3 or len(title) > 90:
        raise ValidationError("Topic titles must be between 3 and 90 characters")
    return title


def _clean_summary(value: str) -> str:
    return " ".join(value.split())[:2000]


async def _get_topic(
    session: AsyncSession,
    scope: CourseScope,
    topic_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Topic:
    topic = (
        await topics_repo.get_for_update(session, topic_id)
        if for_update
        else await topics_repo.get(session, topic_id)
    )
    if topic is None or not scope.includes(topic.course_id):
        raise NotFoundError("Topic not found")
    return topic


async def _get_document(
    session: AsyncSession,
    scope: CourseScope,
    document_id: uuid.UUID,
) -> Document:
    document = await documents_repo.get(session, document_id)
    if document is None or not scope.includes(document.course_id):
        raise NotFoundError("Document not found")
    return document


async def _sync_topic_count(session: AsyncSession, document_id: uuid.UUID) -> None:
    """Keep ``documents.topic_count`` truthful after review mutations."""
    document = await documents_repo.get(session, document_id)
    if document is not None:
        document.topic_count = await topics_repo.count_active_for_document(session, document_id)


async def _assert_unique_title(
    session: AsyncSession,
    document_id: uuid.UUID,
    title: str,
    *,
    exclude_topic_id: uuid.UUID | None = None,
) -> None:
    existing = await topics_repo.list_for_document(session, document_id)
    needle = title.casefold()
    if any(topic.title.casefold() == needle and topic.id != exclude_topic_id for topic in existing):
        raise ConflictError("Another topic in this document already has that title")


async def serialize_topics(
    session: AsyncSession,
    topics: list[Topic],
) -> list[TopicOut]:
    if not topics:
        return []
    counts = await topics_repo.chunk_counts(session, [topic.id for topic in topics])
    tasks = await topics_repo.tasks_for_topics(session, [topic.id for topic in topics])
    result: list[TopicOut] = []
    for topic in topics:
        task = tasks.get(topic.id)
        result.append(
            TopicOut(
                id=topic.id,
                document_id=topic.document_id,
                course_id=topic.course_id,
                title=topic.title,
                summary=topic.summary,
                page_start=topic.page_start,
                page_end=topic.page_end,
                order_index=topic.order_index,
                chunk_count=counts.get(topic.id, 0),
                release_state=topic.release_state,
                released_at=topic.released_at,
                needs_review=topic.needs_review,
                task_id=task.id if task is not None else None,
                task_status=task.status if task is not None else None,
                created_at=topic.created_at,
                updated_at=topic.updated_at,
            )
        )
    return result


async def list_document_topics(
    session: AsyncSession,
    *,
    scope: CourseScope,
    document_id: uuid.UUID,
) -> list[TopicOut]:
    await _get_document(session, scope, document_id)
    topics = await topics_repo.list_for_document(session, document_id)
    return await serialize_topics(session, topics)


async def update_topic(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    topic_id: uuid.UUID,
    payload: TopicUpdate,
    ip: str | None = None,
    request_id: str | None = None,
) -> TopicOut:
    topic = await _get_topic(session, scope, topic_id)
    document = await documents_repo.get(session, topic.document_id)
    if document is None:
        raise NotFoundError("Document not found")

    changes = payload.model_dump(exclude_unset=True)
    if (
        topic.release_state is TopicReleaseState.RELEASED
        and {
            "page_start",
            "page_end",
            "order_index",
        }
        & changes.keys()
    ):
        raise ConflictError("Unrelease the topic before changing its pages or order")

    before = _snapshot(topic)
    if "title" in changes:
        title = _clean_title(str(changes["title"]))
        await _assert_unique_title(session, topic.document_id, title, exclude_topic_id=topic.id)
        topic.title = title
        topic.needs_review = False
    if "summary" in changes:
        topic.summary = _clean_summary(str(changes["summary"]))
        topic.needs_review = False

    if {"page_start", "page_end"} & changes.keys():
        page_start = int(changes.get("page_start", topic.page_start))
        page_end = int(changes.get("page_end", topic.page_end))
        page_count = document.page_count or page_end
        if page_start < 1 or page_end > page_count or page_start > page_end:
            raise ValidationError(f"Page ranges must be inside 1-{page_count} with start <= end")
        topic.page_start = page_start
        topic.page_end = page_end

    if "order_index" in changes:
        active = await topics_repo.list_for_document(session, topic.document_id)
        active = [item for item in active if item.id != topic.id]
        position = min(max(0, int(changes["order_index"])), len(active))
        active.insert(position, topic)
        for index, item in enumerate(active):
            item.order_index = index

    task = await task_service.get_for_topic(session, topic.id)
    task_service.sync_published_copy(task, topic)

    await audit_service.record(
        session,
        actor_id=actor.id,
        action="topic.update",
        entity_type="topic",
        entity_id=topic.id,
        before=before,
        after=_snapshot(topic),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    serialized = await serialize_topics(session, [topic])
    return serialized[0]


async def merge_topics(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    payload: TopicMerge,
    ip: str | None = None,
    request_id: str | None = None,
) -> TopicOut:
    unique_ids = list(dict.fromkeys(payload.topic_ids))
    if len(unique_ids) < 2:
        raise ValidationError("Merging requires at least two distinct topics")

    topics: list[Topic] = []
    for topic_id in unique_ids:
        topics.append(await _get_topic(session, scope, topic_id, for_update=True))
    topics.sort(key=lambda item: (item.order_index, item.page_start, str(item.id)))

    document_id = topics[0].document_id
    if any(topic.document_id != document_id for topic in topics):
        raise ValidationError("Topics must belong to the same document")
    if any(topic.release_state is not TopicReleaseState.DRAFT for topic in topics):
        raise ConflictError("Only draft topics can be merged")

    title = _clean_title(payload.title)
    await _assert_unique_title(
        session,
        document_id,
        title,
        exclude_topic_id=topics[0].id,
    )

    target = topics[0]
    others = topics[1:]
    await topics_repo.reassign_chunks(session, [topic.id for topic in others], target.id)

    target.title = title
    target.page_start = min(topic.page_start for topic in topics)
    target.page_end = max(topic.page_end for topic in topics)
    target.needs_review = any(topic.needs_review for topic in topics)
    if not target.summary:
        target.summary = next((topic.summary for topic in topics if topic.summary), "")

    now = datetime.now(UTC)
    for topic in others:
        topic.deleted_at = now
    await session.flush()
    await topics_repo.renumber(session, document_id)
    await _sync_topic_count(session, document_id)

    before = {"topics": [_snapshot(topic) for topic in topics]}
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="topic.merge",
        entity_type="topic",
        entity_id=target.id,
        before=before,
        after=_snapshot(target),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    serialized = await serialize_topics(session, [target])
    return serialized[0]


async def split_topic(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    topic_id: uuid.UUID,
    payload: TopicSplit,
    ip: str | None = None,
    request_id: str | None = None,
) -> TopicOut:
    topic = await _get_topic(session, scope, topic_id, for_update=True)
    if topic.release_state is not TopicReleaseState.DRAFT:
        raise ConflictError("Only draft topics can be split")

    chunks = await topics_repo.list_chunks(session, topic.id)
    position = next(
        (index for index, chunk in enumerate(chunks) if chunk.id == payload.at_chunk_id),
        None,
    )
    if position is None:
        raise ValidationError("The split chunk does not belong to this topic")
    if position == 0:
        raise ValidationError("Choose a chunk after the first one")

    title = _clean_title(payload.new_title)
    await _assert_unique_title(session, topic.document_id, title)

    moved = chunks[position:]
    before = _snapshot(topic)
    new_topic = Topic(
        document_id=topic.document_id,
        course_id=topic.course_id,
        title=title,
        summary=" ".join(moved[0].text.split())[:300],
        page_start=min(chunk.page_start for chunk in moved),
        page_end=max(chunk.page_end for chunk in moved),
        order_index=topic.order_index + 1,
        release_state=TopicReleaseState.DRAFT,
        needs_review=False,
    )
    session.add(new_topic)
    await session.flush()
    await topics_repo.set_chunk_topic(session, [chunk.id for chunk in moved], new_topic.id)

    remaining = chunks[:position]
    topic.page_start = min(chunk.page_start for chunk in remaining)
    topic.page_end = max(chunk.page_end for chunk in remaining)
    topic.summary = " ".join(remaining[0].text.split())[:300]

    await topics_repo.shift_orders(session, topic.document_id, topic.order_index + 1)
    await session.flush()
    # ``shift_orders`` moved every topic at or after the new position, including
    # the new row. Reload it so the assignment below is a real change, put it
    # directly after its parent, and renumber deterministically.
    await session.refresh(new_topic)
    new_topic.order_index = topic.order_index + 1
    await session.flush()
    await topics_repo.renumber(session, topic.document_id)
    await _sync_topic_count(session, topic.document_id)

    await audit_service.record(
        session,
        actor_id=actor.id,
        action="topic.split",
        entity_type="topic",
        entity_id=topic.id,
        before=before,
        after={"source": _snapshot(topic), "created": _snapshot(new_topic)},
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    serialized = await serialize_topics(session, [new_topic])
    return serialized[0]


async def delete_topic(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    topic_id: uuid.UUID,
    ip: str | None = None,
    request_id: str | None = None,
) -> None:
    topic = await _get_topic(session, scope, topic_id, for_update=True)
    if topic.release_state is not TopicReleaseState.DRAFT:
        raise ConflictError("Unrelease the topic before deleting it")

    active = await topics_repo.list_for_document(session, topic.document_id)
    chunks = await topics_repo.list_chunks(session, topic.id)
    if chunks and len(active) <= 1:
        raise ConflictError("A document must keep at least one topic with content")

    neighbor: Topic | None = None
    if chunks:
        index = next(index for index, item in enumerate(active) if item.id == topic.id)
        neighbor = active[index - 1] if index > 0 else active[index + 1]
        await topics_repo.reassign_chunks(session, [topic.id], neighbor.id)
        neighbor_chunks = await topics_repo.list_chunks(session, neighbor.id)
        if neighbor_chunks:
            neighbor.page_start = min(chunk.page_start for chunk in neighbor_chunks)
            neighbor.page_end = max(chunk.page_end for chunk in neighbor_chunks)

    topic.deleted_at = datetime.now(UTC)
    await session.flush()
    await topics_repo.renumber(session, topic.document_id)
    await _sync_topic_count(session, topic.document_id)

    await audit_service.record(
        session,
        actor_id=actor.id,
        action="topic.delete",
        entity_type="topic",
        entity_id=topic.id,
        before=_snapshot(topic),
        after={"deleted_at": topic.deleted_at.isoformat()},
        ip=ip,
        request_id=request_id,
    )
    await session.commit()


def _student_topic(topic: Topic, chunk_count: int) -> StudentTopicView:
    return StudentTopicView(
        id=topic.id,
        course_id=topic.course_id,
        document_id=topic.document_id,
        title=topic.title,
        summary=topic.summary,
        page_start=topic.page_start,
        page_end=topic.page_end,
        chunk_count=chunk_count,
    )


def _student_chunk(chunk: Chunk) -> StudentChunkView:
    return StudentChunkView(
        id=chunk.id,
        text=chunk.text,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        section_title=chunk.section_title,
    )


async def preview_topic(
    session: AsyncSession,
    *,
    scope: CourseScope,
    topic_id: uuid.UUID,
) -> StudentTopicDetail:
    topic = await _get_topic(session, scope, topic_id)
    chunks = await topics_repo.list_chunks(session, topic.id)
    return StudentTopicDetail(
        topic=_student_topic(topic, len(chunks)),
        chunks=[_student_chunk(chunk) for chunk in chunks],
    )


async def list_student_topics(
    session: AsyncSession,
    *,
    scope: CourseScope,
    course_id: uuid.UUID | None = None,
) -> list[StudentTopicView]:
    if course_id is not None:
        scope.require(course_id)
    topics = await topics_repo.list_released_for_scope(session, scope, course_id=course_id)
    counts = await topics_repo.chunk_counts(session, [topic.id for topic in topics])
    return [_student_topic(topic, counts.get(topic.id, 0)) for topic in topics]


async def get_student_topic(
    session: AsyncSession,
    *,
    scope: CourseScope,
    topic_id: uuid.UUID,
) -> StudentTopicDetail:
    topic = await topics_repo.get(session, topic_id)
    if topic is None or not scope.includes(topic.course_id):
        raise NotFoundError("Topic not found")
    assert_topic_released(topic)
    chunks = await topics_repo.list_chunks(session, topic.id)
    return StudentTopicDetail(
        topic=_student_topic(topic, len(chunks)),
        chunks=[_student_chunk(chunk) for chunk in chunks],
    )


async def release_topic(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    topic_id: uuid.UUID,
    payload: TopicReleaseRequest,
    vector_store: VectorStore,
    ip: str | None = None,
    request_id: str | None = None,
) -> TopicReleaseResult:
    topic = await _get_topic(session, scope, topic_id, for_update=True)
    document = await documents_repo.get(session, topic.document_id)
    if document is None:
        raise NotFoundError("Document not found")
    if document.status is not DocumentStatus.READY:
        raise ConflictError("Wait for topic review to finish before releasing")

    chunks = await topics_repo.list_chunks(session, topic.id)
    if not chunks:
        raise ValidationError("A topic needs at least one chunk before it is released")

    if topic.release_state is TopicReleaseState.RELEASED:
        task = await task_service.get_for_topic(session, topic.id)
        if task is None:
            raise ConflictError("The released topic has no published task")
        quiz = await quizzes_repo.get_for_topic(session, topic.id)
        quiz_created = False
        if payload.generate_quiz and quiz is None:
            quiz, quiz_created = await quiz_service.ensure_quiz_for_topic(session, topic)
            await session.commit()
        quiz_queued = False
        if quiz_created and quiz is not None:
            try:
                quiz_queued = await enqueue_quiz_generation(quiz.id)
            except Exception:
                logger.exception("quiz_enqueue_failed", quiz_id=str(quiz.id))
        return TopicReleaseResult(
            topic_id=topic.id,
            task_id=task.id,
            task_status=task.status,
            released_at=topic.released_at or datetime.now(UTC),
            chroma_synced=True,
            reconcile_queued=False,
            quiz_id=quiz.id if quiz is not None else None,
            quiz_queued=quiz_queued,
        )
    if topic.release_state is TopicReleaseState.SCHEDULED:
        raise ConflictError("Scheduled release is not implemented")

    before = _snapshot(topic)
    released_at = datetime.now(UTC)
    topic.release_state = TopicReleaseState.RELEASED
    topic.released_by = actor.id
    topic.released_at = released_at
    task = await task_service.upsert_published(
        session,
        topic,
        week_no=payload.week_no,
        due_at=payload.due_at,
    )
    quiz = None
    quiz_created = False
    if payload.generate_quiz:
        quiz, quiz_created = await quiz_service.ensure_quiz_for_topic(session, topic)
    await topics_repo.set_topic_released(session, topic.id, released=True)
    await session.flush()
    await notification_service.fan_out_task_release(session, task=task, topic=topic)

    await audit_service.record(
        session,
        actor_id=actor.id,
        action="topic.release",
        entity_type="topic",
        entity_id=topic.id,
        before=before,
        after=_snapshot(topic),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    await analytics_service.invalidate_course(topic.course_id)

    quiz_queued = False
    if quiz_created and quiz is not None:
        try:
            quiz_queued = await enqueue_quiz_generation(quiz.id)
        except Exception:
            # Generation is best-effort: the release itself already committed.
            logger.exception("quiz_enqueue_failed", quiz_id=str(quiz.id))

    chroma_synced = True
    reconcile_queued = False
    try:
        _update_vector_gate(vector_store, chunks, topic.id, released=True)
    except VectorStoreError:
        chroma_synced = False
        reconcile_queued = await enqueue_reconcile()
        logger.warning(
            "release_vector_sync_failed",
            topic_id=str(topic.id),
            reconcile_queued=reconcile_queued,
        )

    return TopicReleaseResult(
        topic_id=topic.id,
        task_id=task.id,
        task_status=task.status,
        released_at=released_at,
        chroma_synced=chroma_synced,
        reconcile_queued=reconcile_queued,
        quiz_id=quiz.id if quiz is not None else None,
        quiz_queued=quiz_queued,
    )


async def unrelease_topic(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    topic_id: uuid.UUID,
    vector_store: VectorStore,
    ip: str | None = None,
    request_id: str | None = None,
) -> TopicUnreleaseResult:
    topic = await _get_topic(session, scope, topic_id, for_update=True)
    if topic.release_state is not TopicReleaseState.RELEASED:
        return TopicUnreleaseResult(
            topic_id=topic.id,
            release_state=topic.release_state,
            chroma_synced=True,
        )

    topic_id_value = topic.id
    chunks = await topics_repo.list_chunks(session, topic.id)

    # Fail closed: hide vectors before the Postgres commit. If Chroma fails the
    # database is untouched, so the topic remains consistently released.
    try:
        _update_vector_gate(vector_store, chunks, topic.id, released=False)
    except VectorStoreError as exc:
        await session.rollback()
        logger.warning("unrelease_vector_sync_failed", topic_id=str(topic_id_value))
        raise ServiceUnavailableError(
            "The release gate could not be updated; please retry",
        ) from exc

    before = _snapshot(topic)
    topic.release_state = TopicReleaseState.DRAFT
    topic.released_by = None
    topic.released_at = None
    await topics_repo.set_topic_released(session, topic.id, released=False)
    await task_service.archive_for_topic(session, topic)

    await audit_service.record(
        session,
        actor_id=actor.id,
        action="topic.unrelease",
        entity_type="topic",
        entity_id=topic.id,
        before=before,
        after=_snapshot(topic),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    await analytics_service.invalidate_course(topic.course_id)
    return TopicUnreleaseResult(
        topic_id=topic.id,
        release_state=TopicReleaseState.DRAFT,
        chroma_synced=True,
    )


def _update_vector_gate(
    vector_store: VectorStore,
    chunks: list[Chunk],
    topic_id: uuid.UUID,
    *,
    released: bool,
) -> None:
    ids = [chunk.vector_id for chunk in chunks if chunk.vector_id]
    if not ids:
        return
    vector_store.update_metadata(
        ids=ids,
        metadatas=[{"released": released, "topic_id": str(topic_id)} for _ in ids],
    )
