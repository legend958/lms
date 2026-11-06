"""Teacher document upload and ingestion status endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_course_scope, request_meta, require_role
from app.api.schemas.documents import DocumentOut, DocumentStatusOut, DocumentUploadOut
from app.core.config import get_settings
from app.core.roles import DocumentStatus, Role
from app.core.scope import CourseScope
from app.db.models import Document, User
from app.db.session import get_session
from app.rag.vector_store import VectorStore, get_vector_store
from app.services import document_service
from app.services.storage import Storage, get_storage
from app.workers.queue import enqueue_ingest

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(require_role(Role.TEACHER, Role.ADMIN))],
)


async def _enqueue_or_mark_failed(session: AsyncSession, document: Document) -> None:
    accepted = await enqueue_ingest(document.id)
    if not accepted and get_settings().ingest_queue != "disabled":
        await document_service.mark_queue_unavailable(session, document)


@router.post("", response_model=DocumentUploadOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    course_id: uuid.UUID = Form(...),
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> DocumentUploadOut:
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    ip, request_id = request_meta(request)
    document = await document_service.upload_document(
        session,
        actor=actor,
        scope=scope,
        course_id=course_id,
        filename=file.filename or "",
        content_type=file.content_type,
        data=data,
        storage=storage,
        ip=ip,
        request_id=request_id,
    )
    await _enqueue_or_mark_failed(session, document)
    return DocumentUploadOut(document_id=document.id, status=document.status)


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    course_id: uuid.UUID | None = None,
    document_status: DocumentStatus | None = Query(default=None, alias="status"),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentOut]:
    return await document_service.list_documents(
        session,
        scope=scope,
        course_id=course_id,
        status=document_status,
    )


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    return await document_service.get_document_out(
        session,
        scope=scope,
        document_id=document_id,
    )


@router.get("/{document_id}/status", response_model=DocumentStatusOut)
async def get_document_status(
    document_id: uuid.UUID,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> DocumentStatusOut:
    return await document_service.get_status(
        session,
        scope=scope,
        document_id=document_id,
    )


@router.post(
    "/{document_id}/retry",
    response_model=DocumentUploadOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_document(
    document_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> DocumentUploadOut:
    ip, request_id = request_meta(request)
    document = await document_service.retry_document(
        session,
        actor=actor,
        scope=scope,
        document_id=document_id,
        storage=storage,
        ip=ip,
        request_id=request_id,
    )
    await _enqueue_or_mark_failed(session, document)
    return DocumentUploadOut(document_id=document.id, status=document.status)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
    vector_store: VectorStore = Depends(get_vector_store),
) -> None:
    ip, request_id = request_meta(request)
    await document_service.delete_document(
        session,
        actor=actor,
        scope=scope,
        document_id=document_id,
        storage=storage,
        vector_store=vector_store,
        ip=ip,
        request_id=request_id,
    )
