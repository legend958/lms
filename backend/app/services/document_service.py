"""Document upload, listing, retry, and deletion."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.documents import DocumentOut, DocumentStatusOut
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.ids import uuid7
from app.core.roles import DocumentStatus, IngestErrorCode, IngestStage
from app.core.scope import CourseScope
from app.db.models import Course, Document, User
from app.db.repositories import courses as courses_repo
from app.db.repositories import documents as documents_repo
from app.db.repositories import topics as topics_repo
from app.rag.extract import EncryptedPdfError, PdfParseError, inspect_pdf
from app.rag.vector_store import VectorStore, VectorStoreError
from app.services import audit_service
from app.services.storage import Storage, StorageError, document_key

logger = structlog.get_logger(__name__)

ALLOWED_CONTENT_TYPES = frozenset(
    {"application/pdf", "application/x-pdf", "application/octet-stream"}
)
PROGRESS_BY_STATUS = {
    DocumentStatus.QUEUED: 0,
    DocumentStatus.EXTRACTING: 5,
    DocumentStatus.CHUNKING: 15,
    DocumentStatus.EMBEDDING: 30,
    DocumentStatus.INDEXED: 100,
    DocumentStatus.TOPIC_EXTRACTION: 90,
    DocumentStatus.READY: 100,
    DocumentStatus.FAILED: 0,
}


def _snapshot(document: Document) -> dict[str, Any]:
    return {
        "course_id": str(document.course_id),
        "filename": document.filename,
        "checksum": document.checksum,
        "status": document.status.value,
        "page_count": document.page_count,
        "topic_count": document.topic_count,
        "topics_source": document.topics_source.value if document.topics_source else None,
        "deleted_at": document.deleted_at.isoformat() if document.deleted_at else None,
    }


def _validate_upload(
    *,
    filename: str,
    content_type: str | None,
    data: bytes,
    settings: Settings,
) -> tuple[str, int]:
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not safe_name or Path(safe_name).suffix.lower() != ".pdf":
        raise ValidationError("Only PDF files can be uploaded")
    if len(safe_name) > 255 or "\x00" in safe_name:
        raise ValidationError("The filename is too long or contains invalid characters")
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type and media_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationError("Only PDF files can be uploaded")
    if not data:
        raise ValidationError("The uploaded file is empty")

    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise ValidationError(f"PDFs must be {settings.max_upload_mb} MB or smaller")
    if b"%PDF-" not in data[:1024]:
        raise ValidationError("The uploaded file is not a valid PDF")

    try:
        info = inspect_pdf(data)
    except EncryptedPdfError as exc:
        raise ValidationError("Encrypted PDFs are not supported") from exc
    except PdfParseError as exc:
        raise ValidationError("The PDF is corrupt or could not be read") from exc

    if info.page_count > settings.max_pdf_pages:
        raise ValidationError(f"PDFs must contain at most {settings.max_pdf_pages} pages")
    return safe_name, info.page_count


async def upload_document(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    course_id: uuid.UUID,
    filename: str,
    content_type: str | None,
    data: bytes,
    storage: Storage,
    ip: str | None = None,
    request_id: str | None = None,
) -> Document:
    settings = get_settings()
    scope.require(course_id)
    course = await courses_repo.get(session, course_id)
    if course is None:
        raise NotFoundError("Course not found")

    safe_name, page_count = _validate_upload(
        filename=filename,
        content_type=content_type,
        data=data,
        settings=settings,
    )
    checksum = hashlib.sha256(data).hexdigest()
    duplicate = await documents_repo.get_by_checksum(session, course_id, checksum)
    if duplicate is not None:
        raise ConflictError(
            "This PDF has already been uploaded to this course",
            extra={"document_id": str(duplicate.id)},
        )

    document = Document(
        id=uuid7(),
        course_id=course_id,
        uploaded_by=actor.id,
        filename=safe_name,
        checksum=checksum,
        size_bytes=len(data),
        page_count=page_count,
        status=DocumentStatus.QUEUED,
        pipeline_version=1,
        topic_count=0,
    )
    session.add(document)
    await session.flush()
    document_id = document.id
    await documents_repo.create_job(session, document_id, stage=IngestStage.QUEUED)

    key = document_key(course_id, document_id)
    try:
        storage.put(key, data)
    except StorageError as exc:
        await session.rollback()
        raise ValidationError("The PDF could not be stored") from exc

    try:
        await audit_service.record(
            session,
            actor_id=actor.id,
            action="document.upload",
            entity_type="document",
            entity_id=document_id,
            after=_snapshot(document),
            ip=ip,
            request_id=request_id,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        try:
            storage.delete(key)
        except StorageError:
            logger.warning("upload_cleanup_failed", document_id=str(document_id))
        raise ConflictError("This PDF has already been uploaded to this course") from exc
    except Exception:
        await session.rollback()
        try:
            storage.delete(key)
        except StorageError:
            logger.warning("upload_cleanup_failed", document_id=str(document_id))
        raise

    logger.info(
        "document_uploaded",
        document_id=str(document_id),
        course_id=str(course_id),
        size_bytes=len(data),
        page_count=page_count,
    )
    return document


async def serialize_documents(
    session: AsyncSession,
    documents: list[Document],
) -> list[DocumentOut]:
    if not documents:
        return []

    course_ids = {document.course_id for document in documents}
    uploader_ids = {document.uploaded_by for document in documents}
    course_rows = await session.execute(
        select(Course.id, Course.code, Course.title).where(Course.id.in_(course_ids))
    )
    courses = {course_id: (code, title) for course_id, code, title in course_rows.all()}
    user_rows = await session.execute(select(User.id, User.name).where(User.id.in_(uploader_ids)))
    users = {user_id: name for user_id, name in user_rows.all()}
    counts = await documents_repo.chunk_counts(session, [document.id for document in documents])

    result: list[DocumentOut] = []
    for document in documents:
        code, title = courses.get(document.course_id, ("", ""))
        result.append(
            DocumentOut(
                id=document.id,
                course_id=document.course_id,
                course_code=code,
                course_title=title,
                uploaded_by=document.uploaded_by,
                uploader_name=users.get(document.uploaded_by, ""),
                filename=document.filename,
                checksum=document.checksum,
                size_bytes=document.size_bytes,
                page_count=document.page_count,
                status=document.status,
                error_code=document.error_code,
                error_detail=document.error_detail,
                pipeline_version=document.pipeline_version,
                topic_count=document.topic_count,
                topics_source=document.topics_source,
                chunk_count=counts.get(document.id, 0),
                created_at=document.created_at,
                updated_at=document.updated_at,
            )
        )
    return result


async def list_documents(
    session: AsyncSession,
    *,
    scope: CourseScope,
    course_id: uuid.UUID | None = None,
    status: DocumentStatus | None = None,
) -> list[DocumentOut]:
    if course_id is not None:
        scope.require(course_id)
    documents = await documents_repo.list_for_scope(
        session, scope, course_id=course_id, status=status
    )
    return await serialize_documents(session, documents)


async def get_document(
    session: AsyncSession,
    *,
    scope: CourseScope,
    document_id: uuid.UUID,
) -> Document:
    document = await documents_repo.get(session, document_id)
    if document is None or not scope.includes(document.course_id):
        raise NotFoundError("Document not found")
    return document


async def get_document_out(
    session: AsyncSession,
    *,
    scope: CourseScope,
    document_id: uuid.UUID,
) -> DocumentOut:
    document = await get_document(session, scope=scope, document_id=document_id)
    serialized = await serialize_documents(session, [document])
    return serialized[0]


async def get_status(
    session: AsyncSession,
    *,
    scope: CourseScope,
    document_id: uuid.UUID,
) -> DocumentStatusOut:
    document = await get_document(session, scope=scope, document_id=document_id)
    job = await documents_repo.get_latest_job(session, document.id)
    chunk_count = await documents_repo.count_chunks(session, document.id)
    return DocumentStatusOut(
        document_id=document.id,
        status=document.status,
        stage=job.stage.value if job is not None else document.status.value,
        progress_pct=job.progress_pct if job is not None else PROGRESS_BY_STATUS[document.status],
        attempts=job.attempts if job is not None else 0,
        error_code=document.error_code,
        error_detail=document.error_detail,
        started_at=job.started_at if job is not None else None,
        finished_at=job.finished_at if job is not None else None,
        chunk_count=chunk_count,
    )


async def retry_document(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    document_id: uuid.UUID,
    storage: Storage,
    ip: str | None = None,
    request_id: str | None = None,
) -> Document:
    document = await get_document(session, scope=scope, document_id=document_id)
    if document.status is not DocumentStatus.FAILED:
        raise ConflictError("Only failed documents can be retried")

    key = document_key(document.course_id, document.id)
    if not storage.exists(key):
        raise ValidationError("The original PDF is missing; upload it again")

    before = _snapshot(document)
    document.status = DocumentStatus.QUEUED
    document.error_code = None
    document.error_detail = None
    await documents_repo.create_job(session, document.id, stage=IngestStage.QUEUED)
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


async def mark_queue_unavailable(
    session: AsyncSession,
    document: Document,
    *,
    detail: str = "The ingestion queue is unavailable; retry the document",
) -> None:
    """Record an enqueue failure without losing the uploaded document."""
    document.status = DocumentStatus.FAILED
    document.error_code = IngestErrorCode.QUEUE_UNAVAILABLE.value
    document.error_detail = detail
    job = await documents_repo.get_latest_job(session, document.id)
    if job is not None:
        job.stage = IngestStage.FAILED
        job.error = detail
        job.finished_at = datetime.now(UTC)
    await session.commit()
    logger.warning("document_queue_unavailable", document_id=str(document.id))


async def delete_document(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    document_id: uuid.UUID,
    storage: Storage,
    vector_store: VectorStore,
    ip: str | None = None,
    request_id: str | None = None,
) -> None:
    document = await get_document(session, scope=scope, document_id=document_id)
    if await topics_repo.count_released_for_document(session, document.id) > 0:
        raise ConflictError("Unrelease this document's topics before deleting it")
    before = _snapshot(document)
    document.deleted_at = datetime.now(UTC)
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="document.delete",
        entity_type="document",
        entity_id=document.id,
        before=before,
        after=_snapshot(document),
        ip=ip,
        request_id=request_id,
    )
    await session.commit()

    try:
        vector_store.delete(where={"document_id": str(document.id)})
    except VectorStoreError:
        logger.warning("document_vector_delete_failed", document_id=str(document.id))

    try:
        storage.delete(document_key(document.course_id, document.id))
    except StorageError:
        logger.warning("document_storage_delete_failed", document_id=str(document.id))
