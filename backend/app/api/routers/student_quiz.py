"""Student quiz listing, attempt runner, autosave, and results."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_course_scope, require_role
from app.api.schemas.quizzes import (
    AttemptSaveRequest,
    AttemptSaveResult,
    AttemptSubmitRequest,
    StudentAttemptList,
    StudentAttemptResult,
    StudentAttemptState,
    StudentQuizDetail,
    StudentQuizOut,
)
from app.core.roles import Role
from app.core.scope import CourseScope
from app.db.session import get_session
from app.services import attempt_service

router = APIRouter(
    prefix="/student",
    tags=["student-quizzes"],
    dependencies=[Depends(require_role(Role.STUDENT))],
)


@router.get("/quizzes", response_model=list[StudentQuizOut])
async def list_student_quizzes(
    course_id: uuid.UUID | None = Query(default=None),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> list[StudentQuizOut]:
    return await attempt_service.list_student_quizzes(
        session,
        scope=scope,
        course_id=course_id,
    )


@router.get("/quizzes/{quiz_id}", response_model=StudentQuizDetail)
async def get_student_quiz(
    quiz_id: uuid.UUID,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> StudentQuizDetail:
    return await attempt_service.get_student_quiz(session, scope=scope, quiz_id=quiz_id)


@router.post("/quizzes/{quiz_id}/attempts", response_model=StudentAttemptState)
async def start_attempt(
    quiz_id: uuid.UUID,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> StudentAttemptState:
    return await attempt_service.start_attempt(session, scope=scope, quiz_id=quiz_id)


@router.get("/attempts", response_model=StudentAttemptList)
async def list_attempts(
    quiz_id: uuid.UUID | None = Query(default=None),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> StudentAttemptList:
    return await attempt_service.list_attempts(session, scope=scope, quiz_id=quiz_id)


@router.get("/attempts/{attempt_id}", response_model=StudentAttemptState)
async def get_attempt(
    attempt_id: uuid.UUID,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> StudentAttemptState:
    return await attempt_service.get_attempt_state(
        session,
        scope=scope,
        attempt_id=attempt_id,
    )


@router.patch("/attempts/{attempt_id}", response_model=AttemptSaveResult)
async def save_attempt(
    attempt_id: uuid.UUID,
    payload: AttemptSaveRequest,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> AttemptSaveResult:
    return await attempt_service.save_attempt(
        session,
        scope=scope,
        attempt_id=attempt_id,
        payload_answers=payload.answers,
    )


@router.post("/attempts/{attempt_id}/submit", response_model=StudentAttemptResult)
async def submit_attempt(
    attempt_id: uuid.UUID,
    payload: AttemptSubmitRequest,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> StudentAttemptResult:
    return await attempt_service.submit_attempt(
        session,
        scope=scope,
        attempt_id=attempt_id,
        payload_answers=payload.answers,
    )


@router.get("/attempts/{attempt_id}/result", response_model=StudentAttemptResult)
async def get_attempt_result(
    attempt_id: uuid.UUID,
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
) -> StudentAttemptResult:
    return await attempt_service.get_attempt_result(
        session,
        scope=scope,
        attempt_id=attempt_id,
    )
