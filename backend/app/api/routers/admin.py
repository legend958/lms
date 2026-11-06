"""Admin oversight endpoints: audit, content, imports, permissions, health."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import request_meta, require_role
from app.api.schemas.admin import (
    AuditLogOut,
    ContentActionOut,
    ContentDownloadOut,
    ContentRow,
    ImportReport,
    PermissionOut,
    PermissionUpdate,
    SystemHealthOut,
)
from app.api.schemas.common import Page
from app.core.config import Settings, get_settings
from app.core.roles import DocumentStatus, Role
from app.db.models import User
from app.db.session import get_session
from app.rag.vector_store import VectorStore, get_vector_store
from app.services import admin_service, document_service, import_service, permission_service
from app.services.storage import Storage, get_storage
from app.workers.queue import enqueue_ingest

router = APIRouter(
    prefix="/admin",
    tags=["admin:oversight"],
    dependencies=[Depends(require_role(Role.ADMIN))],
)


@router.get("/audit-logs", response_model=Page[AuditLogOut])
async def list_audit_logs(
    actor_id: uuid.UUID | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> Page[AuditLogOut]:
    items, next_cursor = await admin_service.list_audit_logs(
        session,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )
    return Page(items=items, next_cursor=next_cursor)


@router.get("/content", response_model=list[ContentRow])
async def list_content(
    course_id: uuid.UUID | None = None,
    document_status: DocumentStatus | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_session),
) -> list[ContentRow]:
    return await admin_service.list_content(
        session,
        course_id=course_id,
        status=document_status,
    )


@router.post(
    "/content/{document_id}/reingest",
    response_model=ContentActionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reingest_content(
    document_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> ContentActionOut:
    ip, request_id = request_meta(request)
    document = await admin_service.reingest_document(
        session,
        actor=actor,
        document_id=document_id,
        ip=ip,
        request_id=request_id,
    )
    accepted = await enqueue_ingest(document.id)
    if not accepted and get_settings().ingest_queue != "disabled":
        await document_service.mark_queue_unavailable(session, document)
    return ContentActionOut(document_id=document.id, status=document.status)


@router.get("/content/{document_id}/download", response_model=ContentDownloadOut)
async def download_content(
    document_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> ContentDownloadOut:
    ip, request_id = request_meta(request)
    return await admin_service.download_document(
        session,
        actor=actor,
        document_id=document_id,
        storage=storage,
        ip=ip,
        request_id=request_id,
    )


@router.delete("/content/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_content(
    document_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
    storage: Storage = Depends(get_storage),
    vector_store: VectorStore = Depends(get_vector_store),
) -> None:
    ip, request_id = request_meta(request)
    await admin_service.hard_delete_document(
        session,
        actor=actor,
        document_id=document_id,
        storage=storage,
        vector_store=vector_store,
        ip=ip,
        request_id=request_id,
    )


@router.post("/users/import", response_model=ImportReport)
async def import_users(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form(default="partial"),
    actor: User = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> ImportReport:
    data = await file.read(import_service.MAX_IMPORT_BYTES + 1)
    ip, request_id = request_meta(request)
    return await import_service.import_users(
        session,
        actor=actor,
        data=data,
        mode=mode,
        ip=ip,
        request_id=request_id,
    )


@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(
    session: AsyncSession = Depends(get_session),
) -> list[PermissionOut]:
    rows = await permission_service.get_permission_service().list_permissions(session)
    return [PermissionOut.model_validate(row) for row in rows]


@router.patch("/permissions", response_model=PermissionOut)
async def update_permission(
    payload: PermissionUpdate,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> PermissionOut:
    ip, request_id = request_meta(request)
    service = permission_service.get_permission_service()
    await service.set_permission(
        session,
        actor=actor,
        key=payload.key,
        role=payload.role,
        enabled=payload.enabled,
        ip=ip,
        request_id=request_id,
    )
    rows = await service.list_permissions(session)
    match = next(row for row in rows if row["key"] == payload.key and row["role"] == payload.role)
    return PermissionOut.model_validate(match)


@router.get("/system/health", response_model=SystemHealthOut)
async def health(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    vector_store: VectorStore = Depends(get_vector_store),
) -> SystemHealthOut:
    return await admin_service.system_health(
        session,
        settings=settings,
        vector_store=vector_store,
    )
