"""Document, ingestion job, and chunk queries."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import DocumentStatus, IngestStage
from app.core.scope import CourseScope
from app.db.models import Chunk, Document, IngestionJob


async def get(
    session: AsyncSession,
    document_id: uuid.UUID,
    *,
    include_deleted: bool = False,
) -> Document | None:
    document = await session.get(Document, document_id)
    if document is None:
        return None
    if document.deleted_at is not None and not include_deleted:
        return None
    return document


async def get_by_checksum(
    session: AsyncSession,
    course_id: uuid.UUID,
    checksum: str,
) -> Document | None:
    stmt = select(Document).where(
        Document.course_id == course_id,
        Document.checksum == checksum,
        Document.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_for_scope(
    session: AsyncSession,
    scope: CourseScope,
    *,
    course_id: uuid.UUID | None = None,
    status: DocumentStatus | None = None,
) -> list[Document]:
    stmt = select(Document).where(Document.deleted_at.is_(None))
    if course_id is not None:
        stmt = stmt.where(Document.course_id == course_id)
    else:
        stmt = stmt.where(Document.course_id.in_(scope.course_ids))
    if status is not None:
        stmt = stmt.where(Document.status == status)
    stmt = stmt.order_by(Document.created_at.desc(), Document.id.desc())
    return list((await session.execute(stmt)).scalars())


async def chunk_counts(
    session: AsyncSession,
    document_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not document_ids:
        return {}
    stmt = (
        select(Chunk.document_id, func.count(Chunk.id))
        .where(Chunk.document_id.in_(document_ids))
        .group_by(Chunk.document_id)
    )
    return {document_id: count for document_id, count in (await session.execute(stmt)).all()}


async def count_chunks(session: AsyncSession, document_id: uuid.UUID) -> int:
    stmt = select(func.count(Chunk.id)).where(Chunk.document_id == document_id)
    return int((await session.execute(stmt)).scalar_one())


async def list_chunks(session: AsyncSession, document_id: uuid.UUID) -> list[Chunk]:
    stmt = select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
    return list((await session.execute(stmt)).scalars())


async def delete_chunks(session: AsyncSession, document_id: uuid.UUID) -> None:
    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))


async def get_latest_job(
    session: AsyncSession,
    document_id: uuid.UUID,
) -> IngestionJob | None:
    stmt = (
        select(IngestionJob)
        .where(IngestionJob.document_id == document_id)
        .order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_job(
    session: AsyncSession,
    document_id: uuid.UUID,
    *,
    stage: IngestStage = IngestStage.QUEUED,
) -> IngestionJob:
    job = IngestionJob(
        document_id=document_id,
        stage=stage,
        progress_pct=0,
        attempts=0,
    )
    session.add(job)
    await session.flush()
    return job


async def list_document_ids(session: AsyncSession) -> list[uuid.UUID]:
    stmt = select(Document.id).where(Document.deleted_at.is_(None))
    return list((await session.execute(stmt)).scalars())
