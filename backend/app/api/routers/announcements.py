"""Announcement endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_course_scope, get_current_user, request_meta
from app.api.schemas.announcements import (
    AnnouncementCreate,
    AnnouncementOut,
    AnnouncementUpdate,
)
from app.core.errors import ForbiddenError, NotFoundError
from app.core.roles import Role
from app.core.scope import CourseScope
from app.db.models import User
from app.db.repositories import announcements as announcements_repo
from app.db.session import get_session
from app.services import announcement_service

router = APIRouter(prefix="/announcements", tags=["announcements"])


@router.get("", response_model=list[AnnouncementOut])
async def list_announcements(
    scheduled: bool = Query(default=False),
    user: User = Depends(get_current_user),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> list[AnnouncementOut]:
    if scheduled and user.role is Role.STUDENT:
        raise ForbiddenError("Students cannot view scheduled announcements")
    rows = await announcements_repo.list_visible(
        session,
        scope,
        now=datetime.now(UTC),
        include_scheduled=scheduled and user.role is not Role.STUDENT,
    )
    return await announcement_service.serialize(session, rows, user.id)


@router.post("", response_model=AnnouncementOut, status_code=201)
async def create_announcement(
    payload: AnnouncementCreate,
    request: Request,
    user: User = Depends(get_current_user),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> AnnouncementOut:
    ip, request_id = request_meta(request)
    announcement = await announcement_service.create(
        session,
        actor=user,
        scope=scope,
        title=payload.title,
        body=payload.body,
        course_id=payload.course_id,
        publish_at=payload.publish_at,
        expires_at=payload.expires_at,
        ip=ip,
        request_id=request_id,
    )
    serialized = await announcement_service.serialize(session, [announcement], user.id)
    return serialized[0]


@router.patch("/{announcement_id}", response_model=AnnouncementOut)
async def update_announcement(
    announcement_id: uuid.UUID,
    payload: AnnouncementUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> AnnouncementOut:
    ip, request_id = request_meta(request)
    announcement = await announcement_service.update(
        session,
        actor=user,
        scope=scope,
        announcement_id=announcement_id,
        changes=payload.model_dump(exclude_unset=True),
        ip=ip,
        request_id=request_id,
    )
    serialized = await announcement_service.serialize(session, [announcement], user.id)
    return serialized[0]


@router.delete("/{announcement_id}", status_code=204)
async def delete_announcement(
    announcement_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> None:
    ip, request_id = request_meta(request)
    await announcement_service.delete(
        session,
        actor=user,
        scope=scope,
        announcement_id=announcement_id,
        ip=ip,
        request_id=request_id,
    )


@router.post("/{announcement_id}/read", status_code=204)
async def mark_announcement_read(
    announcement_id: uuid.UUID,
    user: User = Depends(get_current_user),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> None:
    announcement = await announcements_repo.get(session, announcement_id)
    if announcement is None:
        raise NotFoundError("Announcement not found")
    if not scope.is_admin and announcement.course_id is not None:
        scope.require(announcement.course_id)
    await announcements_repo.mark_read(session, announcement_id, user.id)
    await session.commit()
