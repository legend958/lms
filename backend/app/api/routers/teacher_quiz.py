"""Teacher quiz review, generation, and publishing endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_course_scope, request_meta, require_role
from app.api.schemas.quizzes import (
    QuestionUpdate,
    QuizCreate,
    QuizDetail,
    QuizList,
    QuizOut,
    QuizUpdate,
    TeacherQuestionOut,
)
from app.core.roles import QuizStatus, Role
from app.core.scope import CourseScope
from app.db.models import User
from app.db.session import get_session
from app.services import quiz_service

router = APIRouter(
    prefix="/teacher",
    tags=["teacher-quizzes"],
    dependencies=[Depends(require_role(Role.TEACHER, Role.ADMIN))],
)


@router.get("/quizzes", response_model=QuizList)
async def list_quizzes(
    course_id: uuid.UUID | None = Query(default=None),
    status_filter: QuizStatus | None = Query(default=None, alias="status"),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> QuizList:
    items = await quiz_service.list_quizzes(
        session,
        scope=scope,
        course_id=course_id,
        status=status_filter,
    )
    return QuizList(items=items)


@router.get("/quizzes/{quiz_id}", response_model=QuizDetail)
async def get_quiz(
    quiz_id: uuid.UUID,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> QuizDetail:
    return await quiz_service.get_quiz(session, scope=scope, quiz_id=quiz_id)


@router.get("/topics/{topic_id}/quiz", response_model=QuizDetail)
async def get_quiz_for_topic(
    topic_id: uuid.UUID,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> QuizDetail:
    return await quiz_service.get_quiz_for_topic(session, scope=scope, topic_id=topic_id)


@router.post("/topics/{topic_id}/quiz", response_model=QuizDetail)
async def create_quiz(
    topic_id: uuid.UUID,
    payload: QuizCreate,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> QuizDetail:
    ip, request_id = request_meta(request)
    return await quiz_service.create_quiz_for_topic(
        session,
        actor=actor,
        scope=scope,
        topic_id=topic_id,
        payload=payload,
        ip=ip,
        request_id=request_id,
    )


@router.patch("/quizzes/{quiz_id}", response_model=QuizDetail)
async def update_quiz(
    quiz_id: uuid.UUID,
    payload: QuizUpdate,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> QuizDetail:
    ip, request_id = request_meta(request)
    return await quiz_service.update_quiz(
        session,
        actor=actor,
        scope=scope,
        quiz_id=quiz_id,
        payload=payload,
        ip=ip,
        request_id=request_id,
    )


@router.post("/quizzes/{quiz_id}/approve", response_model=QuizDetail)
async def approve_quiz(
    quiz_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> QuizDetail:
    ip, request_id = request_meta(request)
    return await quiz_service.approve_quiz(
        session,
        actor=actor,
        scope=scope,
        quiz_id=quiz_id,
        ip=ip,
        request_id=request_id,
    )


@router.post("/quizzes/{quiz_id}/regenerate", response_model=QuizOut)
async def regenerate_quiz(
    quiz_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> QuizOut:
    ip, request_id = request_meta(request)
    detail = await quiz_service.regenerate_quiz(
        session,
        actor=actor,
        scope=scope,
        quiz_id=quiz_id,
        ip=ip,
        request_id=request_id,
    )
    return QuizOut(**detail.model_dump(exclude={"questions"}))


@router.patch("/questions/{question_id}", response_model=TeacherQuestionOut)
async def update_question(
    question_id: uuid.UUID,
    payload: QuestionUpdate,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> TeacherQuestionOut:
    ip, request_id = request_meta(request)
    return await quiz_service.update_question(
        session,
        actor=actor,
        scope=scope,
        question_id=question_id,
        payload=payload,
        ip=ip,
        request_id=request_id,
    )


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(
    question_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role(Role.TEACHER, Role.ADMIN)),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> None:
    ip, request_id = request_meta(request)
    await quiz_service.delete_question(
        session,
        actor=actor,
        scope=scope,
        question_id=question_id,
        ip=ip,
        request_id=request_id,
    )
