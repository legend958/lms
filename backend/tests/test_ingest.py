from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.core.roles import DocumentStatus, IngestErrorCode, Role, TopicReleaseState
from app.core.scope import CourseScope
from app.db.models import Document
from app.db.repositories import documents as documents_repo
from app.db.repositories import topics as topics_repo
from app.db.session import get_sessionmaker
from app.services.document_service import upload_document
from app.services.ingestion_service import DocumentMissingError, IngestError, run_ingest
from app.services.storage import LocalStorage, document_key
from app.workers.ingest import _should_retry
from tests.fakes import FakeEmbedder, FakeVectorStore
from tests.pdf_utils import make_blank_pdf_bytes, make_encrypted_pdf_bytes, make_pdf_bytes


def _scope(world) -> CourseScope:  # type: ignore[no-untyped-def]
    return CourseScope(
        user_id=world.teacher.id,
        role=Role.TEACHER,
        course_ids=frozenset({world.course_a.id}),
    )


async def test_ingest_indexes_chunks_and_is_idempotent(
    world, session: AsyncSession, tmp_path
) -> None:
    storage = LocalStorage(tmp_path)
    document = await upload_document(
        session,
        actor=world.teacher,
        scope=_scope(world),
        course_id=world.course_a.id,
        filename="lesson.pdf",
        content_type="application/pdf",
        data=make_pdf_bytes(pages=2),
        storage=storage,
    )
    vector_store = FakeVectorStore()
    embedder = FakeEmbedder()

    await run_ingest(
        document.id,
        storage=storage,
        vector_store=vector_store,
        embedder=embedder,
    )

    async with get_sessionmaker()() as db:
        stored = await documents_repo.get(db, document.id)
        chunks = await documents_repo.list_chunks(db, document.id)
        topics = await topics_repo.list_for_document(db, document.id)
        assert stored is not None
        assert stored.status is DocumentStatus.READY
        assert stored.topics_source is not None
        assert stored.topic_count == len(topics) > 0
        assert stored.page_count == 2
        assert stored.error_code is None
        assert chunks
        assert all(chunk.released is False for chunk in chunks)
        assert all(chunk.topic_id is not None for chunk in chunks)
        assert all(topic.release_state is TopicReleaseState.DRAFT for topic in topics)
        assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
        assert all(chunk.vector_id == f"{document.id}:{chunk.chunk_index}" for chunk in chunks)
        assert {chunk.topic_id for chunk in chunks} == {topic.id for topic in topics}

    assert vector_store.count() == len(chunks)
    first_count = vector_store.count()
    first_vectors = set(vector_store.records)
    assert all(record["metadata"]["released"] is False for record in vector_store.records.values())
    assert all(
        record["metadata"]["topic_id"] != "unassigned" for record in vector_store.records.values()
    )

    await run_ingest(
        document.id,
        storage=storage,
        vector_store=vector_store,
        embedder=embedder,
    )

    async with get_sessionmaker()() as db:
        chunks_after = await documents_repo.list_chunks(db, document.id)
        topics_after = await topics_repo.list_for_document(db, document.id)
    assert vector_store.count() == first_count == len(chunks_after)
    assert set(vector_store.records) == first_vectors
    assert len(topics_after) > 0


async def _insert_document(
    session: AsyncSession,
    world,
    storage: LocalStorage,
    data: bytes,
    *,
    filename: str,
) -> uuid.UUID:
    document_id = uuid7()
    storage.put(document_key(world.course_a.id, document_id), data)
    document = Document(
        id=document_id,
        course_id=world.course_a.id,
        uploaded_by=world.teacher.id,
        filename=filename,
        checksum=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        status=DocumentStatus.QUEUED,
        pipeline_version=1,
        topic_count=0,
    )
    session.add(document)
    await session.flush()
    await documents_repo.create_job(session, document.id)
    await session.commit()
    return document_id


async def test_encrypted_pdf_fails_with_typed_error(world, session: AsyncSession, tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    document_id = await _insert_document(
        session,
        world,
        storage,
        make_encrypted_pdf_bytes(),
        filename="encrypted.pdf",
    )

    with pytest.raises(IngestError) as caught:
        await run_ingest(
            document_id,
            storage=storage,
            vector_store=FakeVectorStore(),
            embedder=FakeEmbedder(),
        )

    assert caught.value.code is IngestErrorCode.ENCRYPTED_PDF
    async with get_sessionmaker()() as db:
        stored = await documents_repo.get(db, document_id)
        assert stored is not None
        assert stored.status is DocumentStatus.FAILED
        assert stored.error_code == "ENCRYPTED_PDF"


async def test_blank_pdf_fails_with_no_text_layer(world, session: AsyncSession, tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    document_id = await _insert_document(
        session,
        world,
        storage,
        make_blank_pdf_bytes(),
        filename="blank.pdf",
    )

    with pytest.raises(IngestError) as caught:
        await run_ingest(
            document_id,
            storage=storage,
            vector_store=FakeVectorStore(),
            embedder=FakeEmbedder(),
        )

    assert caught.value.code is IngestErrorCode.NO_TEXT_LAYER
    async with get_sessionmaker()() as db:
        stored = await documents_repo.get(db, document_id)
        assert stored is not None
        assert stored.status is DocumentStatus.FAILED
        assert stored.error_code == "NO_TEXT_LAYER"


async def test_reingest_is_blocked_while_a_topic_is_released(
    world, session: AsyncSession, tmp_path
) -> None:
    storage = LocalStorage(tmp_path)
    document = await upload_document(
        session,
        actor=world.teacher,
        scope=_scope(world),
        course_id=world.course_a.id,
        filename="released.pdf",
        content_type="application/pdf",
        data=make_pdf_bytes(pages=2),
        storage=storage,
    )
    vector_store = FakeVectorStore()
    await run_ingest(
        document.id,
        storage=storage,
        vector_store=vector_store,
        embedder=FakeEmbedder(),
    )

    async with get_sessionmaker()() as db:
        topics = await topics_repo.list_for_document(db, document.id)
        assert topics
        topics[0].release_state = TopicReleaseState.RELEASED
        await db.commit()

    with pytest.raises(IngestError) as caught:
        await run_ingest(
            document.id,
            storage=storage,
            vector_store=vector_store,
            embedder=FakeEmbedder(),
        )
    assert caught.value.code is IngestErrorCode.RELEASED_TOPICS

    async with get_sessionmaker()() as db:
        stored = await documents_repo.get(db, document.id)
        assert stored is not None
        assert stored.status is DocumentStatus.READY
        assert stored.error_code is None


def test_worker_retries_only_transient_failures() -> None:
    encrypted = IngestError(IngestErrorCode.ENCRYPTED_PDF, "encrypted")
    no_text = IngestError(IngestErrorCode.NO_TEXT_LAYER, "no text")
    embedding = IngestError(IngestErrorCode.EMBEDDING_FAILED, "model unavailable")
    parse = IngestError(IngestErrorCode.PARSE_FAILED, "parser failed")

    assert _should_retry(encrypted, 1, 3) is False
    assert _should_retry(no_text, 1, 3) is False
    assert _should_retry(DocumentMissingError(), 1, 3) is False
    assert _should_retry(embedding, 1, 3) is True
    assert _should_retry(parse, 1, 3) is True
    assert _should_retry(embedding, 3, 3) is False
