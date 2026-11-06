"""Admin repository: audit viewer, content controls, and system health."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import redis.asyncio as redis
import structlog
from sqlalchemy import case, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.admin import (
    AuditLogOut,
    ContentDownloadOut,
    ContentRow,
    SystemHealthOut,
)
from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.core.roles import DocumentStatus, IngestStage, TopicReleaseState
from app.db.models import (
    AttemptAnswer,
    AuditLog,
    Chunk,
    Course,
    Document,
    IngestionJob,
    Question,
    Quiz,
    QuizAttempt,
    Task,
    TaskProgress,
    Topic,
    User,
)
from app.db.pagination import decode_cursor, encode_cursor
from app.rag.vector_store import VectorStore, VectorStoreError
from app.services import audit_service
from app.services.storage import Storage, StorageError, document_key

logger = structlog.get_logger(__name__)

DOWNLOAD_TTL_SECONDS = 300
IN_PROGRESS_STATUSES = {
    DocumentStatus.QUEUED,
    DocumentStatus.EXTRACTING,
    DocumentStatus.CHUNKING,
    DocumentStatus.EMBEDDING,
    DocumentStatus.INDEXED,
    DocumentStatus.TOPIC_EXTRACTION,
}


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _snapshot(document: Document) -> dict[str, Any]:
    return {
        "course_id": str(document.course_id),
        "filename": document.filename,
        "checksum": document.checksum,
        "size_bytes": document.size_bytes,
        "page_count": document.page_count,
        "status": document.status.value,
        "topic_count": document.topic_count,
        "topics_source": document.topics_source.value if document.topics_source else None,
    }


async def list_audit_logs(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[AuditLogOut], str | None]:
    stmt = select(AuditLog, User.name).outerjoin(User, User.id == AuditLog.actor_id)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"%{action.strip()}%"))
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type.strip())
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id.strip())
    if date_from is not None:
        stmt = stmt.where(AuditLog.created_at >= _as_utc(date_from))
    if date_to is not None:
        stmt = stmt.where(AuditLog.created_at <= _as_utc(date_to))
    if cursor:
        cursor_time, cursor_id = decode_cursor(cursor)
        cursor_time = _as_utc(cursor_time)
        stmt = stmt.where(
            (AuditLog.created_at < cursor_time)
            | ((AuditLog.created_at == cursor_time) & (AuditLog.id < cursor_id))
        )

    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1)
    rows = list((await session.execute(stmt)).all())
    next_cursor: str | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        last_entry = rows[-1][0]
        next_cursor = encode_cursor(_as_utc(last_entry.created_at), last_entry.id)

    return (
        [
            AuditLogOut(
                id=entry.id,
                actor_id=entry.actor_id,
                actor_name=actor_name,
                action=entry.action,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                before=entry.before,
                after=entry.after,
                ip=entry.ip,
                request_id=entry.request_id,
                created_at=_as_utc(entry.created_at),
            )
            for entry, actor_name in rows
        ],
        next_cursor,
    )


async def list_content(
    session: AsyncSession,
    *,
    course_id: uuid.UUID | None = None,
    status: DocumentStatus | None = None,
) -> list[ContentRow]:
    topic_stats = (
        select(
            Topic.document_id.label("document_id"),
            func.count(Topic.id).label("topic_count"),
            func.coalesce(
                func.sum(
                    case(
                        (Topic.release_state == TopicReleaseState.RELEASED, 1),
                        else_=0,
                    )
                ),
                0,
            ).label("released_topics"),
        )
        .where(Topic.deleted_at.is_(None))
        .group_by(Topic.document_id)
        .subquery()
    )
    chunk_stats = (
        select(
            Chunk.document_id.label("document_id"),
            func.count(Chunk.id).label("chunk_count"),
        )
        .group_by(Chunk.document_id)
        .subquery()
    )

    stmt = (
        select(
            Document,
            Course.code,
            Course.title,
            User.name,
            func.coalesce(topic_stats.c.topic_count, 0),
            func.coalesce(topic_stats.c.released_topics, 0),
            func.coalesce(chunk_stats.c.chunk_count, 0),
        )
        .join(Course, Course.id == Document.course_id)
        .join(User, User.id == Document.uploaded_by)
        .outerjoin(topic_stats, topic_stats.c.document_id == Document.id)
        .outerjoin(chunk_stats, chunk_stats.c.document_id == Document.id)
        .where(Document.deleted_at.is_(None))
    )
    if course_id is not None:
        stmt = stmt.where(Document.course_id == course_id)
    if status is not None:
        stmt = stmt.where(Document.status == status)
    stmt = stmt.order_by(Document.created_at.desc(), Document.id.desc())

    rows = (await session.execute(stmt)).all()
    return [
        ContentRow(
            id=document.id,
            course_id=document.course_id,
            course_code=course_code,
            course_title=course_title,
            uploaded_by=document.uploaded_by,
            uploader_name=uploader_name,
            filename=document.filename,
            size_bytes=document.size_bytes,
            page_count=document.page_count,
            status=document.status,
            error_code=document.error_code,
            topic_count=int(topic_count),
            released_topics=int(released_topics),
            chunk_count=int(chunk_count),
            topics_source=document.topics_source,
            created_at=_as_utc(document.created_at),
            updated_at=_as_utc(document.updated_at),
        )
        for (
            document,
            course_code,
            course_title,
            uploader_name,
            topic_count,
            released_topics,
            chunk_count,
        ) in rows
    ]


async def _get_content_document(session: AsyncSession, document_id: uuid.UUID) -> Document:
    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise NotFoundError("Document not found")
    return document


async def hard_delete_document(
    session: AsyncSession,
    *,
    actor: User,
    document_id: uuid.UUID,
    storage: Storage,
    vector_store: VectorStore,
    ip: str | None = None,
    request_id: str | None = None,
) -> None:
    document = await _get_content_document(session, document_id)
    released = int(
        (
            await session.execute(
                select(func.count(Topic.id)).where(
                    Topic.document_id == document.id,
                    Topic.deleted_at.is_(None),
                    Topic.release_state == TopicReleaseState.RELEASED,
                )
            )
        ).scalar_one()
    )
    if released:
        raise ConflictError("Unrelease this document's topics before deleting it")

    before = _snapshot(document)
    topic_ids = select(Topic.id).where(Topic.document_id == document.id)
    quiz_ids = select(Quiz.id).where(Quiz.topic_id.in_(topic_ids))
    question_ids = select(Question.id).where(Question.quiz_id.in_(quiz_ids))
    task_ids = select(Task.id).where(Task.topic_id.in_(topic_ids))

    await session.execute(delete(AttemptAnswer).where(AttemptAnswer.question_id.in_(question_ids)))
    await session.execute(delete(QuizAttempt).where(QuizAttempt.quiz_id.in_(quiz_ids)))
    await session.execute(delete(Question).where(Question.quiz_id.in_(quiz_ids)))
    await session.execute(delete(Quiz).where(Quiz.topic_id.in_(topic_ids)))
    await session.execute(delete(TaskProgress).where(TaskProgress.task_id.in_(task_ids)))
    await session.execute(delete(Task).where(Task.topic_id.in_(topic_ids)))
    await session.execute(delete(Topic).where(Topic.document_id == document.id))
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    await session.execute(delete(IngestionJob).where(IngestionJob.document_id == document.id))
    await session.delete(document)

    await audit_service.record(
        session,
        actor_id=actor.id,
        action="document.delete",
        entity_type="document",
        entity_id=document.id,
        before=before,
        after={"hard_deleted": True, "released_topics": 0},
        ip=ip,
        request_id=request_id,
    )
    await session.commit()

    try:
        vector_store.delete(where={"document_id": str(document.id)})
    except VectorStoreError:
        logger.warning("admin_document_vector_delete_failed", document_id=str(document.id))

    try:
        storage.delete(document_key(document.course_id, document.id))
    except StorageError:
        logger.warning("admin_document_storage_delete_failed", document_id=str(document.id))

    logger.info("admin_document_deleted", document_id=str(document.id), actor_id=str(actor.id))


async def reingest_document(
    session: AsyncSession,
    *,
    actor: User,
    document_id: uuid.UUID,
    ip: str | None = None,
    request_id: str | None = None,
) -> Document:
    document = await _get_content_document(session, document_id)
    if document.status in IN_PROGRESS_STATUSES:
        raise ConflictError("This document is already being ingested")
    released = int(
        (
            await session.execute(
                select(func.count(Topic.id)).where(
                    Topic.document_id == document.id,
                    Topic.deleted_at.is_(None),
                    Topic.release_state == TopicReleaseState.RELEASED,
                )
            )
        ).scalar_one()
    )
    if released:
        raise ConflictError("Unrelease this document's topics before re-ingesting it")

    before = _snapshot(document)
    document.status = DocumentStatus.QUEUED
    document.error_code = None
    document.error_detail = None
    await session.flush()
    job = IngestionJob(document_id=document.id, stage=IngestStage.QUEUED, progress_pct=0)
    session.add(job)
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="document.reingest",
        entity_type="document",
        entity_id=document.id,
        before=before,
        after=_snapshot(document),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    return document


async def download_document(
    session: AsyncSession,
    *,
    actor: User,
    document_id: uuid.UUID,
    storage: Storage,
    ip: str | None = None,
    request_id: str | None = None,
) -> ContentDownloadOut:
    document = await _get_content_document(session, document_id)
    key = document_key(document.course_id, document.id)
    if not storage.exists(key):
        raise NotFoundError("The original PDF is missing from storage")

    url = storage.signed_url(key, ttl_seconds=DOWNLOAD_TTL_SECONDS)
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="document.download",
        entity_type="document",
        entity_id=document.id,
        after={"filename": document.filename, "ttl_seconds": DOWNLOAD_TTL_SECONDS},
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    return ContentDownloadOut(
        document_id=document.id,
        filename=document.filename,
        url=url,
        expires_in=DOWNLOAD_TTL_SECONDS,
    )


async def check_groq(settings: Settings) -> str:
    if not settings.groq_api_key:
        return "not_configured"
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            )
        return "ok" if response.status_code == 200 else "degraded"
    except Exception:
        return "degraded"


async def system_health(
    session: AsyncSession,
    *,
    settings: Settings,
    vector_store: VectorStore,
) -> SystemHealthOut:
    checks: dict[str, str] = {}
    healthy = True
    queue_depth: int | None = None
    client: redis.Redis[str] | None = None

    try:
        await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "fail"
        healthy = False

    if settings.redis_url:
        client = redis.from_url(settings.redis_url, socket_connect_timeout=1)
        try:
            await client.ping()
            checks["redis"] = "ok"
            try:
                queue_depth = int(await client.zcard("arq:queue"))
            except Exception:
                queue_depth = None
        except Exception:
            checks["redis"] = "fail"
            healthy = False
        finally:
            await client.aclose()  # type: ignore[attr-defined]
    else:
        checks["redis"] = "disabled"

    try:
        await asyncio.wait_for(asyncio.to_thread(vector_store.count), timeout=3)
        checks["chroma"] = "ok"
    except Exception:
        checks["chroma"] = "fail"
        healthy = False

    checks["groq"] = await check_groq(settings)
    if checks["groq"] == "degraded":
        healthy = False

    failure_row = (
        await session.execute(
            select(Document, Course.code)
            .join(Course, Course.id == Document.course_id)
            .where(
                Document.status == DocumentStatus.FAILED,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.updated_at.desc())
            .limit(1)
        )
    ).first()
    last_failure: dict[str, Any] | None = None
    if failure_row is not None:
        document, course_code = failure_row
        last_failure = {
            "document_id": str(document.id),
            "filename": document.filename,
            "course_code": course_code,
            "error_code": document.error_code,
            "error_detail": document.error_detail,
            "updated_at": _as_utc(document.updated_at).isoformat(),
        }

    return SystemHealthOut(
        status="ok" if healthy else "degraded",
        checks=checks,
        queue_depth=queue_depth,
        last_ingestion_failure=last_failure,
        generated_at=datetime.now(UTC),
    )


__all__ = [
    "download_document",
    "hard_delete_document",
    "list_audit_logs",
    "list_content",
    "reingest_document",
    "system_health",
]
