"""Quiz, question, and attempt queries.

Student-facing reads always join ``enrollments`` and re-check the topic release
gate, exactly like the task repository. A quiz is only visible when it is
published, its topic is released and not deleted, its document is not deleted,
the course is active, and the student is enrolled.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import (
    CourseRole,
    CourseStatus,
    QuizStatus,
    TopicReleaseState,
)
from app.core.scope import CourseScope
from app.db.models import (
    AttemptAnswer,
    Course,
    Document,
    Enrollment,
    Question,
    Quiz,
    QuizAttempt,
    Topic,
)


@dataclass(slots=True)
class QuestionCounts:
    total: int = 0
    approved: int = 0


@dataclass(slots=True)
class AttemptStats:
    submitted: int = 0
    open_attempt_id: uuid.UUID | None = None
    best_percentage: int | None = None


def _percentage(score: int | None, max_score: int | None) -> int | None:
    if score is None or not max_score:
        return None
    return round(score * 100 / max_score)


async def get(session: AsyncSession, quiz_id: uuid.UUID) -> Quiz | None:
    return await session.get(Quiz, quiz_id)


async def get_for_update(session: AsyncSession, quiz_id: uuid.UUID) -> Quiz | None:
    stmt = select(Quiz).where(Quiz.id == quiz_id).with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_for_topic(session: AsyncSession, topic_id: uuid.UUID) -> Quiz | None:
    stmt = select(Quiz).where(Quiz.topic_id == topic_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_for_scope(
    session: AsyncSession,
    scope: CourseScope,
    *,
    course_id: uuid.UUID | None = None,
    status: QuizStatus | None = None,
) -> list[tuple[Quiz, Topic, Course]]:
    """Teacher quiz list. The course scope is the authorization boundary."""
    stmt = (
        select(Quiz, Topic, Course)
        .join(Topic, Topic.id == Quiz.topic_id)
        .join(Course, Course.id == Quiz.course_id)
        .where(
            Quiz.course_id.in_(scope.course_ids),
            Topic.deleted_at.is_(None),
        )
    )
    if course_id is not None:
        stmt = stmt.where(Quiz.course_id == course_id)
    if status is not None:
        stmt = stmt.where(Quiz.status == status)
    stmt = stmt.order_by(Course.code, Topic.order_index, Quiz.id)
    rows = (await session.execute(stmt)).all()
    return [(quiz, topic, course) for quiz, topic, course in rows]


async def list_questions(
    session: AsyncSession,
    quiz_id: uuid.UUID,
    *,
    approved_only: bool = False,
) -> list[Question]:
    stmt = select(Question).where(Question.quiz_id == quiz_id)
    if approved_only:
        stmt = stmt.where(Question.approved.is_(True))
    stmt = stmt.order_by(Question.order_index, Question.id)
    return list((await session.execute(stmt)).scalars())


async def get_question(session: AsyncSession, question_id: uuid.UUID) -> Question | None:
    return await session.get(Question, question_id)


async def question_counts(
    session: AsyncSession,
    quiz_ids: list[uuid.UUID],
) -> dict[uuid.UUID, QuestionCounts]:
    if not quiz_ids:
        return {}
    stmt = select(Question.quiz_id, Question.approved).where(Question.quiz_id.in_(quiz_ids))
    counts: dict[uuid.UUID, QuestionCounts] = {}
    for quiz_id, approved in (await session.execute(stmt)).all():
        item = counts.setdefault(quiz_id, QuestionCounts())
        item.total += 1
        if approved:
            item.approved += 1
    return counts


async def delete_questions(session: AsyncSession, quiz_id: uuid.UUID) -> None:
    await session.execute(delete(Question).where(Question.quiz_id == quiz_id))


async def count_attempts_for_quiz(session: AsyncSession, quiz_id: uuid.UUID) -> int:
    stmt = select(func.count(QuizAttempt.id)).where(QuizAttempt.quiz_id == quiz_id)
    return int((await session.execute(stmt)).scalar_one())


async def attempt_counts(
    session: AsyncSession,
    quiz_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not quiz_ids:
        return {}
    stmt = (
        select(QuizAttempt.quiz_id, func.count(QuizAttempt.id))
        .where(QuizAttempt.quiz_id.in_(quiz_ids))
        .group_by(QuizAttempt.quiz_id)
    )
    return {quiz_id: int(count) for quiz_id, count in (await session.execute(stmt)).all()}


def _student_quiz_statement(
    user_id: uuid.UUID,
    scope: CourseScope,
) -> Select[Any]:
    return (
        select(Quiz, Topic, Course)
        .join(Topic, Topic.id == Quiz.topic_id)
        .join(Document, Document.id == Topic.document_id)
        .join(Course, Course.id == Quiz.course_id)
        .join(
            Enrollment,
            and_(
                Enrollment.course_id == Quiz.course_id,
                Enrollment.user_id == user_id,
                Enrollment.course_role == CourseRole.STUDENT,
            ),
        )
        .where(
            Quiz.status == QuizStatus.PUBLISHED,
            Quiz.course_id.in_(scope.course_ids),
            Topic.course_id == Quiz.course_id,
            Topic.release_state == TopicReleaseState.RELEASED,
            Topic.deleted_at.is_(None),
            Document.deleted_at.is_(None),
            Course.status == CourseStatus.ACTIVE,
        )
    )


async def list_student_quizzes(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    scope: CourseScope,
    course_id: uuid.UUID | None = None,
) -> list[tuple[Quiz, Topic, Course]]:
    if not scope.course_ids:
        return []
    stmt = _student_quiz_statement(user_id, scope)
    if course_id is not None:
        stmt = stmt.where(Quiz.course_id == course_id)
    stmt = stmt.order_by(Course.code, Topic.order_index, Quiz.id)
    rows = (await session.execute(stmt)).all()
    return [(quiz, topic, course) for quiz, topic, course in rows]


async def get_student_quiz(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    scope: CourseScope,
    quiz_id: uuid.UUID,
) -> tuple[Quiz, Topic, Course] | None:
    if not scope.course_ids:
        return None
    stmt = _student_quiz_statement(user_id, scope).where(Quiz.id == quiz_id)
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return (row[0], row[1], row[2])


async def get_open_attempt(
    session: AsyncSession,
    *,
    student_id: uuid.UUID,
    quiz_id: uuid.UUID,
) -> QuizAttempt | None:
    stmt = select(QuizAttempt).where(
        QuizAttempt.student_id == student_id,
        QuizAttempt.quiz_id == quiz_id,
        QuizAttempt.submitted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_attempt(session: AsyncSession, attempt_id: uuid.UUID) -> QuizAttempt | None:
    return await session.get(QuizAttempt, attempt_id)


async def get_attempt_for_student(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    student_id: uuid.UUID,
) -> QuizAttempt | None:
    stmt = select(QuizAttempt).where(
        QuizAttempt.id == attempt_id,
        QuizAttempt.student_id == student_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_attempt_for_update(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    student_id: uuid.UUID,
) -> QuizAttempt | None:
    stmt = (
        select(QuizAttempt)
        .where(
            QuizAttempt.id == attempt_id,
            QuizAttempt.student_id == student_id,
        )
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_attempts_for_student(
    session: AsyncSession,
    *,
    student_id: uuid.UUID,
    quiz_id: uuid.UUID | None = None,
) -> list[QuizAttempt]:
    stmt = select(QuizAttempt).where(QuizAttempt.student_id == student_id)
    if quiz_id is not None:
        stmt = stmt.where(QuizAttempt.quiz_id == quiz_id)
    stmt = stmt.order_by(QuizAttempt.started_at.desc(), QuizAttempt.id)
    return list((await session.execute(stmt)).scalars())


async def count_submitted_attempts(
    session: AsyncSession,
    *,
    student_id: uuid.UUID,
    quiz_id: uuid.UUID,
) -> int:
    stmt = select(func.count(QuizAttempt.id)).where(
        QuizAttempt.student_id == student_id,
        QuizAttempt.quiz_id == quiz_id,
        QuizAttempt.submitted_at.is_not(None),
    )
    return int((await session.execute(stmt)).scalar_one())


async def attempt_stats(
    session: AsyncSession,
    *,
    student_id: uuid.UUID,
    quiz_ids: list[uuid.UUID],
) -> dict[uuid.UUID, AttemptStats]:
    if not quiz_ids:
        return {}
    stmt = select(QuizAttempt).where(
        QuizAttempt.student_id == student_id,
        QuizAttempt.quiz_id.in_(quiz_ids),
    )
    stats: dict[uuid.UUID, AttemptStats] = {}
    for attempt in (await session.execute(stmt)).scalars():
        item = stats.setdefault(attempt.quiz_id, AttemptStats())
        if attempt.submitted_at is None:
            item.open_attempt_id = attempt.id
            continue
        item.submitted += 1
        percentage = _percentage(attempt.score, attempt.max_score)
        if percentage is not None and (
            item.best_percentage is None or percentage > item.best_percentage
        ):
            item.best_percentage = percentage
    return stats


async def list_answers(
    session: AsyncSession,
    attempt_id: uuid.UUID,
) -> list[AttemptAnswer]:
    stmt = select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt_id)
    return list((await session.execute(stmt)).scalars())


async def get_answer(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    question_id: uuid.UUID,
) -> AttemptAnswer | None:
    stmt = select(AttemptAnswer).where(
        AttemptAnswer.attempt_id == attempt_id,
        AttemptAnswer.question_id == question_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()
