"""Teacher topic review, release, and student read endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_course_scope, request_meta, require_role
from app.api.schemas.topics import (
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
from app.core.roles import Role
from app.core.scope import CourseScope
from app.db.models import User
from app.db.session import get_session
from app.rag.vector_store import VectorStore, get_vector_store
from app.services import topic_service

router = APIRouter(
    tags=["topics"],
    dependencies=[Depends(require_role(Role.TEACHER, Role.ADMIN))],
)

student_router = APIRouter(
    prefix="/student/topics",
    tags=["student-topics"],
    dependencies=[Depends(require_role(Role.STUDENT))],
)


@router.get("/documents/{document_id}/topics", response_model=list[TopicOut])
async def list_document_topics(
    document_id: uuid.UUID,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> list[TopicOut]:
    return await topic_service.list_document_topics(
        session,
        scope=scope,
        document_id=document_id,
    )


@router.post("/topics/merge", response_model=TopicOut)
async def merge_topics(
    payload: TopicMerge,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> TopicOut:
    ip, request_id = request_meta(request)
    return await topic_service.merge_topics(
        session,
        actor=actor,
        scope=scope,
        payload=payload,
        ip=ip,
        request_id=request_id,
    )


@router.patch("/topics/{topic_id}", response_model=TopicOut)
async def update_topic(
    topic_id: uuid.UUID,
    payload: TopicUpdate,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> TopicOut:
    ip, request_id = request_meta(request)
    return await topic_service.update_topic(
        session,
        actor=actor,
        scope=scope,
        topic_id=topic_id,
        payload=payload,
        ip=ip,
        request_id=request_id,
    )


@router.post("/topics/{topic_id}/split", response_model=TopicOut)
async def split_topic(
    topic_id: uuid.UUID,
    payload: TopicSplit,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> TopicOut:
    ip, request_id = request_meta(request)
    return await topic_service.split_topic(
        session,
        actor=actor,
        scope=scope,
        topic_id=topic_id,
        payload=payload,
        ip=ip,
        request_id=request_id,
    )


@router.delete("/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic(
    topic_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> None:
    ip, request_id = request_meta(request)
    await topic_service.delete_topic(
        session,
        actor=actor,
        scope=scope,
        topic_id=topic_id,
        ip=ip,
        request_id=request_id,
    )


@router.get("/topics/{topic_id}/preview", response_model=StudentTopicDetail)
async def preview_topic(
    topic_id: uuid.UUID,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> StudentTopicDetail:
    return await topic_service.preview_topic(
        session,
        scope=scope,
        topic_id=topic_id,
    )


@router.post("/topics/{topic_id}/release", response_model=TopicReleaseResult)
async def release_topic(
    topic_id: uuid.UUID,
    payload: TopicReleaseRequest,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
    vector_store: VectorStore = Depends(get_vector_store),
) -> TopicReleaseResult:
    ip, request_id = request_meta(request)
    return await topic_service.release_topic(
        session,
        actor=actor,
        scope=scope,
        topic_id=topic_id,
        payload=payload,
        vector_store=vector_store,
        ip=ip,
        request_id=request_id,
    )


@router.post("/topics/{topic_id}/unrelease", response_model=TopicUnreleaseResult)
async def unrelease_topic(
    topic_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
    vector_store: VectorStore = Depends(get_vector_store),
) -> TopicUnreleaseResult:
    ip, request_id = request_meta(request)
    return await topic_service.unrelease_topic(
        session,
        actor=actor,
        scope=scope,
        topic_id=topic_id,
        vector_store=vector_store,
        ip=ip,
        request_id=request_id,
    )


@student_router.get("", response_model=list[StudentTopicView])
async def list_student_topics(
    course_id: uuid.UUID | None = Query(default=None),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> list[StudentTopicView]:
    return await topic_service.list_student_topics(
        session,
        scope=scope,
        course_id=course_id,
    )


@student_router.get("/{topic_id}", response_model=StudentTopicDetail)
async def get_student_topic(
    topic_id: uuid.UUID,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> StudentTopicDetail:
    return await topic_service.get_student_topic(
        session,
        scope=scope,
        topic_id=topic_id,
    )
