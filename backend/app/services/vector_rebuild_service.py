"""Rebuild the vector index from authoritative Postgres chunk rows.

Chroma is a derived index, not a source of truth. When the vector service is
replaced (a new Render deploy, a wiped volume, a changed embedding model), the
index can be regenerated without re-extracting PDFs or re-running topic
extraction: every chunk's text and release metadata already lives in Postgres.

This is deliberately not part of request handling. It is an operational command
used by the deploy pipeline.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.roles import DocumentStatus, TopicReleaseState
from app.db.models import Chunk, Document, Topic
from app.rag.embedder import TextEmbedder
from app.rag.vector_store import VectorStore

logger = structlog.get_logger(__name__)


def _metadata(chunk: Chunk, topic: Topic | None, document: Document) -> dict[str, Any]:
    visible = topic if topic is not None and topic.deleted_at is None else None
    released = (
        visible is not None
        and visible.release_state is TopicReleaseState.RELEASED
        and document.status is DocumentStatus.READY
        and document.deleted_at is None
    )
    return {
        "course_id": str(chunk.course_id),
        "document_id": str(chunk.document_id),
        "topic_id": str(chunk.topic_id) if visible is not None else "unassigned",
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "section_title": chunk.section_title or "",
        "released": released,
        "pipeline_version": document.pipeline_version,
        "chunk_index": chunk.chunk_index,
    }


async def rebuild_vectors(
    session: AsyncSession,
    *,
    settings: Settings,
    embedder: TextEmbedder,
    vector_store: VectorStore,
) -> int:
    """Replace the collection with a fresh embedding of every visible chunk.

    Returns the number of vectors written. An empty database is a no-op so a
    misconfigured connection cannot silently erase a healthy index.
    """
    result = await session.execute(
        select(Chunk, Topic, Document)
        .outerjoin(Topic, Chunk.topic_id == Topic.id)
        .join(Document, Chunk.document_id == Document.id)
        .where(Document.deleted_at.is_(None))
        .order_by(Chunk.document_id, Chunk.chunk_index)
    )
    rows = list(result.all())
    if not rows:
        logger.warning("vector_rebuild_empty")
        return 0

    vector_store.reset()
    written = 0
    batch_size = max(1, settings.embedding_batch_size)
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        texts = [chunk.text for chunk, _topic, _document in batch]
        embeddings = await asyncio.to_thread(
            embedder.encode,
            texts,
            batch_size=batch_size,
        )
        vector_store.upsert(
            ids=[chunk.vector_id for chunk, _topic, _document in batch],
            embeddings=embeddings,
            documents=texts,
            metadatas=[_metadata(chunk, topic, document) for chunk, topic, document in batch],
        )
        written += len(batch)

    logger.info("vector_rebuild_complete", vectors=written)
    return written
