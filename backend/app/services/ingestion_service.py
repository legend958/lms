"""Synchronous ingestion pipeline shared by the ARQ worker and CLI."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.roles import DocumentStatus, IngestErrorCode, IngestStage, TopicReleaseState
from app.db.models import Chunk, Document, IngestionJob, Topic
from app.db.repositories import documents as documents_repo
from app.db.repositories import topics as topics_repo
from app.db.session import get_sessionmaker
from app.rag.chunker import ChunkData, chunk_document
from app.rag.embedder import EmbeddingError, TextEmbedder, get_embedder
from app.rag.extract import EncryptedPdfError, PdfParseError, extract_pdf
from app.rag.topics import (
    TopicChunk,
    TopicExtractionError,
    TopicExtractor,
    fallback_topics,
    get_topic_extractor,
)
from app.rag.vector_store import VectorStore, VectorStoreError, get_vector_store
from app.services.storage import Storage, StorageError, document_key, get_storage

logger = structlog.get_logger(__name__)


class IngestError(RuntimeError):
    def __init__(self, code: IngestErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class DocumentMissingError(IngestError):
    def __init__(self) -> None:
        super().__init__(IngestErrorCode.UNKNOWN, "Document not found")


def _map_error(exc: Exception) -> IngestError:
    if isinstance(exc, IngestError):
        return exc
    if isinstance(exc, EncryptedPdfError):
        return IngestError(IngestErrorCode.ENCRYPTED_PDF, str(exc))
    if isinstance(exc, PdfParseError):
        return IngestError(IngestErrorCode.PARSE_FAILED, str(exc))
    if isinstance(exc, EmbeddingError):
        return IngestError(IngestErrorCode.EMBEDDING_FAILED, str(exc))
    if isinstance(exc, VectorStoreError):
        return IngestError(IngestErrorCode.VECTOR_WRITE_FAILED, str(exc))
    if isinstance(exc, StorageError):
        return IngestError(IngestErrorCode.STORAGE_FAILED, str(exc))
    return IngestError(IngestErrorCode.UNKNOWN, "Ingestion failed")


def _embedding_text(chunk: ChunkData) -> str:
    if chunk.section_title:
        return f"{chunk.section_title}\n{chunk.text}"
    return chunk.text


def _metadata(document: Document, chunk: ChunkData) -> dict[str, Any]:
    return {
        "course_id": str(document.course_id),
        "document_id": str(document.id),
        "topic_id": "unassigned",
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "section_title": chunk.section_title or "",
        "released": False,
        "pipeline_version": document.pipeline_version,
        "chunk_index": chunk.chunk_index,
    }


async def _set_stage(
    session: AsyncSession,
    document: Document,
    job: IngestionJob,
    *,
    status: DocumentStatus,
    stage: IngestStage,
    progress: int,
) -> None:
    document.status = status
    job.stage = stage
    job.progress_pct = max(0, min(100, progress))
    await session.commit()


async def _mark_failed(document_id: uuid.UUID, exc: Exception) -> IngestError:
    error = _map_error(exc)
    try:
        async with get_sessionmaker()() as session:
            document = await documents_repo.get(session, document_id, include_deleted=True)
            if document is None or document.deleted_at is not None:
                return error
            job = await documents_repo.get_latest_job(session, document_id)
            document.status = DocumentStatus.FAILED
            document.error_code = error.code.value
            document.error_detail = error.detail[:2000]
            if job is not None:
                job.stage = IngestStage.FAILED
                job.error = error.detail[:2000]
                job.finished_at = datetime.now(UTC)
            await session.commit()
    except Exception:
        logger.exception("ingest_failure_record_failed", document_id=str(document_id))
    logger.warning(
        "ingest_failed",
        document_id=str(document_id),
        error_code=error.code.value,
        detail=error.detail,
    )
    return error


async def _run_ingest(
    document_id: uuid.UUID,
    *,
    attempt: int,
    storage: Storage,
    vector_store: VectorStore,
    embedder: TextEmbedder,
    extractor: TopicExtractor,
) -> Document:
    settings = get_settings()
    async with get_sessionmaker()() as session:
        document = await documents_repo.get(session, document_id)
        if document is None:
            raise DocumentMissingError

        # Re-ingesting a released document would silently invalidate the
        # release gate, so require an explicit unrelease before any work.
        if await topics_repo.count_released_for_document(session, document.id) > 0:
            raise IngestError(
                IngestErrorCode.RELEASED_TOPICS,
                "Unrelease this document's topics before re-ingesting it",
            )

        job = await documents_repo.get_latest_job(session, document.id)
        if job is None:
            job = await documents_repo.create_job(session, document.id)
        job.attempts += 1
        if attempt > job.attempts:
            job.attempts = attempt
        job.started_at = job.started_at or datetime.now(UTC)
        job.finished_at = None
        job.error = None
        document.error_code = None
        document.error_detail = None
        await _set_stage(
            session,
            document,
            job,
            status=DocumentStatus.EXTRACTING,
            stage=IngestStage.EXTRACTING,
            progress=5,
        )

        key = document_key(document.course_id, document.id)
        try:
            with storage.open(key) as handle:
                data = handle.read()
        except StorageError as exc:
            raise IngestError(IngestErrorCode.STORAGE_FAILED, "Could not read the PDF") from exc

        extracted = extract_pdf(data)
        if not extracted.has_text:
            raise IngestError(
                IngestErrorCode.NO_TEXT_LAYER,
                "The PDF has no extractable text layer",
            )
        document.page_count = extracted.page_count
        await _set_stage(
            session,
            document,
            job,
            status=DocumentStatus.CHUNKING,
            stage=IngestStage.CHUNKING,
            progress=15,
        )

        chunks = chunk_document(
            extracted,
            embedder.count_tokens,
            max_tokens=settings.chunk_tokens,
            overlap=settings.chunk_overlap_tokens,
        )
        if not chunks:
            raise IngestError(
                IngestErrorCode.NO_TEXT_LAYER,
                "No usable text chunks were produced from the PDF",
            )
        await _set_stage(
            session,
            document,
            job,
            status=DocumentStatus.EMBEDDING,
            stage=IngestStage.EMBEDDING,
            progress=30,
        )

        embeddings: list[list[float]] = []
        batch_size = max(1, settings.embedding_batch_size)
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = embedder.encode(
                [_embedding_text(chunk) for chunk in batch],
                batch_size=batch_size,
            )
            embeddings.extend(vectors)
            progress = 30 + int(50 * len(embeddings) / len(chunks))
            job.progress_pct = min(progress, 80)
            await session.commit()

        if len(embeddings) != len(chunks):
            raise IngestError(
                IngestErrorCode.EMBEDDING_FAILED,
                "The embedding model returned an unexpected number of vectors",
            )

        # Replace old vectors, topics, and rows only after every new vector is
        # ready. Topics must go before chunks because chunks reference them.
        vector_store.delete(where={"document_id": str(document.id)})
        await topics_repo.delete_for_document(session, document.id)
        await documents_repo.delete_chunks(session, document.id)
        rows: list[Chunk] = []
        for chunk in chunks:
            rows.append(
                Chunk(
                    vector_id=f"{document.id}:{chunk.chunk_index}",
                    document_id=document.id,
                    course_id=document.course_id,
                    topic_id=None,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    section_title=chunk.section_title,
                    token_count=chunk.token_count,
                    released=False,
                )
            )
        session.add_all(rows)
        await session.flush()
        job.progress_pct = 85
        await session.commit()

        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vector_store.upsert(
                ids=[f"{document.id}:{chunk.chunk_index}" for chunk in batch],
                embeddings=embeddings[start : start + batch_size],
                documents=[chunk.text for chunk in batch],
                metadatas=[_metadata(document, chunk) for chunk in batch],
            )
            progress = 85 + int(14 * min(len(embeddings), start + batch_size) / len(chunks))
            job.progress_pct = min(progress, 99)
            await session.commit()

        await _set_stage(
            session,
            document,
            job,
            status=DocumentStatus.TOPIC_EXTRACTION,
            stage=IngestStage.TOPIC_EXTRACTION,
            progress=92,
        )

        topic_chunks = [
            TopicChunk(
                id=row.id,
                chunk_index=row.chunk_index,
                text=row.text,
                page_start=row.page_start,
                page_end=row.page_end,
                section_title=row.section_title,
            )
            for row in rows
        ]
        try:
            result = await extractor.extract(topic_chunks, extracted.page_count)
            if not result.topics:
                raise TopicExtractionError("The extractor returned no topics")
        except TopicExtractionError as exc:
            logger.warning(
                "topic_extraction_fallback",
                document_id=str(document.id),
                error=str(exc),
            )
            result = fallback_topics(topic_chunks, extracted.page_count)
        if not result.topics:
            raise IngestError(
                IngestErrorCode.UNKNOWN,
                "No topics could be created for this PDF",
            )

        chunk_by_id = {row.id: row for row in rows}
        topic_rows: list[Topic] = []
        for order_index, proposal in enumerate(result.topics):
            topic = Topic(
                document_id=document.id,
                course_id=document.course_id,
                title=proposal.title,
                summary=proposal.summary,
                page_start=proposal.page_start,
                page_end=proposal.page_end,
                order_index=order_index,
                release_state=TopicReleaseState.DRAFT,
                needs_review=proposal.needs_review,
            )
            session.add(topic)
            topic_rows.append(topic)
        await session.flush()

        for topic, proposal in zip(topic_rows, result.topics, strict=True):
            for chunk_id in proposal.chunk_ids:
                chunk_row = chunk_by_id.get(chunk_id)
                if chunk_row is None:
                    raise IngestError(
                        IngestErrorCode.UNKNOWN,
                        "A topic referenced a chunk that no longer exists",
                    )
                chunk_row.topic_id = topic.id
                chunk_row.released = False
        await session.flush()
        if any(row.topic_id is None for row in rows):
            raise IngestError(
                IngestErrorCode.UNKNOWN,
                "Some document chunks were not assigned to a topic",
            )

        for start in range(0, len(rows), batch_size):
            row_batch = rows[start : start + batch_size]
            vector_store.update_metadata(
                ids=[row.vector_id for row in row_batch],
                metadatas=[{"topic_id": str(row.topic_id), "released": False} for row in row_batch],
            )
            job.progress_pct = min(99, 93 + int(6 * (start + len(row_batch)) / len(rows)))
            await session.commit()

        document.topic_count = len(topic_rows)
        document.topics_source = result.source
        document.status = DocumentStatus.READY
        document.error_code = None
        document.error_detail = None
        job.stage = IngestStage.READY
        job.progress_pct = 100
        job.finished_at = datetime.now(UTC)
        job.error = None
        await session.commit()

        logger.info(
            "ingest_complete",
            document_id=str(document.id),
            chunks=len(chunks),
            topics=len(topic_rows),
            topics_source=result.source.value,
            pages=extracted.page_count,
        )
        return document


async def run_ingest(
    document_id: uuid.UUID,
    *,
    attempt: int = 1,
    storage: Storage | None = None,
    vector_store: VectorStore | None = None,
    embedder: TextEmbedder | None = None,
    extractor: TopicExtractor | None = None,
) -> Document:
    """Run one ingestion attempt, recording terminal failure state on errors."""
    try:
        return await _run_ingest(
            document_id,
            attempt=attempt,
            storage=storage or get_storage(),
            vector_store=vector_store or get_vector_store(),
            embedder=embedder or get_embedder(),
            extractor=extractor or get_topic_extractor(),
        )
    except Exception as exc:
        if isinstance(exc, IngestError) and exc.code is IngestErrorCode.RELEASED_TOPICS:
            # A ready document with released topics must keep its state; the
            # caller asked for a re-ingest that cannot proceed safely.
            raise
        error = await _mark_failed(document_id, exc)
        if isinstance(exc, IngestError):
            raise
        raise error from exc


async def reset_for_retry(document_id: uuid.UUID) -> None:
    async with get_sessionmaker()() as session:
        document = await documents_repo.get(session, document_id)
        if document is None:
            return
        job = await documents_repo.get_latest_job(session, document_id)
        document.status = DocumentStatus.QUEUED
        document.error_code = None
        document.error_detail = None
        if job is not None:
            job.stage = IngestStage.QUEUED
            job.progress_pct = 0
            job.error = None
            job.finished_at = None
        await session.commit()


async def reconcile_vectors() -> tuple[int, int]:
    """Repair ``chunks.released`` drift in Chroma metadata.

    Returns ``(checked_topics, repaired_vectors)``. The repair direction is
    always Postgres -> Chroma: Postgres is the source of truth, and a Chroma
    write can only delay visibility, never grant it early.
    """
    vector_store = get_vector_store()
    checked = 0
    repaired = 0
    async with get_sessionmaker()() as session:
        topics = await topics_repo.list_all_active(session)
        for topic in topics:
            chunks = await topics_repo.list_chunks(session, topic.id)
            checked += 1
            if not chunks:
                continue

            expected = {chunk.vector_id: chunk.released for chunk in chunks if chunk.vector_id}
            if not expected:
                continue
            try:
                stored = vector_store.get(ids=list(expected))
            except VectorStoreError:
                logger.warning("vector_reconcile_unavailable", topic_id=str(topic.id))
                continue

            stored_ids = list(stored.get("ids") or [])
            metadatas = list(stored.get("metadatas") or [])
            actual = {
                vector_id: bool(metadata.get("released", False))
                for vector_id, metadata in zip(stored_ids, metadatas, strict=False)
            }
            drift_ids = [
                vector_id
                for vector_id, released in expected.items()
                if actual.get(vector_id, False) is not released
            ]
            if not drift_ids:
                continue

            try:
                vector_store.update_metadata(
                    ids=drift_ids,
                    metadatas=[
                        {
                            "released": expected[vector_id],
                            "topic_id": str(topic.id),
                        }
                        for vector_id in drift_ids
                    ],
                )
            except VectorStoreError:
                logger.warning("vector_reconcile_repair_failed", topic_id=str(topic.id))
                continue
            repaired += len(drift_ids)
            logger.info(
                "vector_drift_repaired",
                topic_id=str(topic.id),
                repaired=len(drift_ids),
            )

    logger.info("vector_reconcile_complete", checked=checked, repaired=repaired)
    return checked, repaired
