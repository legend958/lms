"""Teacher quiz review, async generation orchestration, and publishing.

The LLM only proposes questions; ``app.rag.quizzes`` validates every proposal
against the topic's chunks. This service owns persistence, publishing rules, and
the observable generation state machine.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.quizzes import (
    QuestionUpdate,
    QuizCreate,
    QuizDetail,
    QuizOut,
    QuizUpdate,
    TeacherQuestionOut,
)
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.roles import (
    QuestionDifficulty,
    QuizGenerationStatus,
    QuizStatus,
    Role,
    TopicReleaseState,
)
from app.core.scope import CourseScope
from app.db.models import Question, Quiz, Topic, User
from app.db.repositories import quizzes as quizzes_repo
from app.db.repositories import topics as topics_repo
from app.db.session import get_sessionmaker
from app.rag.quizzes import QuizSourceChunk, get_quiz_generator
from app.services import audit_service, permission_service

logger = structlog.get_logger(__name__)

MAX_GENERATION_ERROR = 1000


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _clean_title(value: str) -> str:
    title = " ".join(value.split())
    if len(title) < 3 or len(title) > 200:
        raise ValidationError("Quiz titles must be between 3 and 200 characters")
    return title


def serialize_question(question: Question) -> TeacherQuestionOut:
    return TeacherQuestionOut(
        id=question.id,
        quiz_id=question.quiz_id,
        stem=question.stem,
        options=list(question.options),
        correct_option=question.correct_option,
        explanation=question.explanation,
        difficulty=question.difficulty,
        source_chunk_id=question.source_chunk_id,
        approved=question.approved,
        order_index=question.order_index,
    )


def serialize_quiz(
    quiz: Quiz,
    topic: Topic,
    *,
    question_count: int = 0,
    approved_count: int = 0,
    attempt_count: int = 0,
) -> QuizOut:
    return QuizOut(
        id=quiz.id,
        topic_id=quiz.topic_id,
        course_id=quiz.course_id,
        topic_title=topic.title,
        title=quiz.title,
        pass_threshold=quiz.pass_threshold,
        attempt_limit=quiz.attempt_limit,
        status=quiz.status,
        auto_approve=quiz.auto_approve,
        needs_review=quiz.needs_review,
        generation_status=quiz.generation_status,
        generation_error=quiz.generation_error,
        question_count=question_count,
        approved_count=approved_count,
        attempt_count=attempt_count,
        created_at=_as_utc(quiz.created_at) or datetime.now(UTC),
        updated_at=_as_utc(quiz.updated_at) or datetime.now(UTC),
    )


async def _detail(
    session: AsyncSession,
    quiz: Quiz,
    topic: Topic,
    *,
    questions: list[Question] | None = None,
) -> QuizDetail:
    counts = (await quizzes_repo.question_counts(session, [quiz.id])).get(quiz.id)
    attempts = (await quizzes_repo.attempt_counts(session, [quiz.id])).get(quiz.id, 0)
    if questions is None:
        questions = await quizzes_repo.list_questions(session, quiz.id)
    base = serialize_quiz(
        quiz,
        topic,
        question_count=counts.total if counts else 0,
        approved_count=counts.approved if counts else 0,
        attempt_count=attempts,
    )
    return QuizDetail(**base.model_dump(), questions=[serialize_question(q) for q in questions])


async def _get_quiz(
    session: AsyncSession,
    scope: CourseScope,
    quiz_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> tuple[Quiz, Topic]:
    quiz = (
        await quizzes_repo.get_for_update(session, quiz_id)
        if for_update
        else await quizzes_repo.get(session, quiz_id)
    )
    if quiz is None or not scope.includes(quiz.course_id):
        raise NotFoundError("Quiz not found")
    topic = await topics_repo.get(session, quiz.topic_id)
    if topic is None:
        raise NotFoundError("Quiz not found")
    return quiz, topic


async def _get_question(
    session: AsyncSession,
    scope: CourseScope,
    question_id: uuid.UUID,
) -> tuple[Question, Quiz, Topic]:
    question = await quizzes_repo.get_question(session, question_id)
    if question is None:
        raise NotFoundError("Question not found")
    quiz, topic = await _get_quiz(session, scope, question.quiz_id, for_update=True)
    return question, quiz, topic


async def list_quizzes(
    session: AsyncSession,
    *,
    scope: CourseScope,
    course_id: uuid.UUID | None = None,
    status: QuizStatus | None = None,
) -> list[QuizOut]:
    if course_id is not None:
        scope.require(course_id)
    rows = await quizzes_repo.list_for_scope(
        session,
        scope,
        course_id=course_id,
        status=status,
    )
    quiz_ids = [quiz.id for quiz, _, _ in rows]
    counts = await quizzes_repo.question_counts(session, quiz_ids)
    attempts = await quizzes_repo.attempt_counts(session, quiz_ids)
    items: list[QuizOut] = []
    for quiz, topic, _course in rows:
        item = counts.get(quiz.id)
        items.append(
            serialize_quiz(
                quiz,
                topic,
                question_count=item.total if item else 0,
                approved_count=item.approved if item else 0,
                attempt_count=attempts.get(quiz.id, 0),
            )
        )
    return items


async def get_quiz(
    session: AsyncSession,
    *,
    scope: CourseScope,
    quiz_id: uuid.UUID,
) -> QuizDetail:
    quiz, topic = await _get_quiz(session, scope, quiz_id)
    return await _detail(session, quiz, topic)


async def get_quiz_for_topic(
    session: AsyncSession,
    *,
    scope: CourseScope,
    topic_id: uuid.UUID,
) -> QuizDetail:
    topic = await topics_repo.get(session, topic_id)
    if topic is None or not scope.includes(topic.course_id):
        raise NotFoundError("Topic not found")
    quiz = await quizzes_repo.get_for_topic(session, topic_id)
    if quiz is None:
        raise NotFoundError("Quiz not found")
    return await _detail(session, quiz, topic)


async def ensure_quiz_for_topic(
    session: AsyncSession,
    topic: Topic,
    *,
    auto_approve: bool = False,
    title: str | None = None,
) -> tuple[Quiz, bool]:
    """Create the topic's quiz if missing. Returns ``(quiz, created)``.

    The caller owns the transaction and must enqueue generation after commit.
    """
    existing = await quizzes_repo.get_for_topic(session, topic.id)
    if existing is not None:
        return existing, False
    quiz = Quiz(
        topic_id=topic.id,
        course_id=topic.course_id,
        title=_clean_title(title or topic.title),
        pass_threshold=60,
        attempt_limit=0,
        status=QuizStatus.DRAFT,
        auto_approve=auto_approve,
        needs_review=True,
        generation_status=QuizGenerationStatus.QUEUED,
    )
    session.add(quiz)
    await session.flush()
    return quiz, True


async def create_quiz_for_topic(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    topic_id: uuid.UUID,
    payload: QuizCreate,
    ip: str | None = None,
    request_id: str | None = None,
) -> QuizDetail:
    topic = await topics_repo.get(session, topic_id)
    if topic is None or not scope.includes(topic.course_id):
        raise NotFoundError("Topic not found")
    if topic.release_state is not TopicReleaseState.RELEASED:
        raise ConflictError("Release the topic before generating a quiz")
    quiz, created = await ensure_quiz_for_topic(
        session,
        topic,
        auto_approve=payload.auto_approve,
        title=payload.title,
    )
    if created:
        await audit_service.record(
            session,
            actor_id=actor.id,
            action="quiz.create",
            entity_type="quiz",
            entity_id=quiz.id,
            after={"topic_id": str(topic.id), "auto_approve": payload.auto_approve},
            ip=ip,
            request_id=request_id,
        )
    await session.commit()
    if created:
        await _enqueue(quiz.id)
    return await _detail(session, quiz, topic)


async def update_quiz(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    quiz_id: uuid.UUID,
    payload: QuizUpdate,
    ip: str | None = None,
    request_id: str | None = None,
) -> QuizDetail:
    quiz, topic = await _get_quiz(session, scope, quiz_id, for_update=True)
    before = {
        "title": quiz.title,
        "pass_threshold": quiz.pass_threshold,
        "attempt_limit": quiz.attempt_limit,
        "auto_approve": quiz.auto_approve,
    }
    if payload.title is not None:
        quiz.title = _clean_title(payload.title)
    if payload.pass_threshold is not None:
        quiz.pass_threshold = payload.pass_threshold
    if payload.attempt_limit is not None:
        quiz.attempt_limit = payload.attempt_limit
    if payload.auto_approve is not None:
        quiz.auto_approve = payload.auto_approve
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="quiz.update",
        entity_type="quiz",
        entity_id=quiz.id,
        before=before,
        after={
            "title": quiz.title,
            "pass_threshold": quiz.pass_threshold,
            "attempt_limit": quiz.attempt_limit,
            "auto_approve": quiz.auto_approve,
        },
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    return await _detail(session, quiz, topic)


async def approve_quiz(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    quiz_id: uuid.UUID,
    ip: str | None = None,
    request_id: str | None = None,
) -> QuizDetail:
    quiz, topic = await _get_quiz(session, scope, quiz_id, for_update=True)
    questions = await quizzes_repo.list_questions(session, quiz.id)
    if not questions:
        raise ConflictError("Generate or add at least one question before approving")
    if quiz.status is not QuizStatus.PUBLISHED:
        for question in questions:
            question.approved = True
        quiz.status = QuizStatus.PUBLISHED
        quiz.needs_review = False
        quiz.generation_error = None
        await audit_service.record(
            session,
            actor_id=actor.id,
            action="quiz.approve",
            entity_type="quiz",
            entity_id=quiz.id,
            after={"status": quiz.status.value, "question_count": len(questions)},
            ip=ip,
            request_id=request_id,
        )
        await session.commit()
    return await _detail(session, quiz, topic)


async def regenerate_quiz(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    quiz_id: uuid.UUID,
    ip: str | None = None,
    request_id: str | None = None,
) -> QuizDetail:
    quiz, topic = await _get_quiz(session, scope, quiz_id, for_update=True)
    if quiz.status is QuizStatus.PUBLISHED:
        raise ConflictError("Regeneration is only available for draft quizzes")
    if await quizzes_repo.count_attempts_for_quiz(session, quiz.id) > 0:
        raise ConflictError("This quiz already has attempts and cannot be regenerated")
    quiz.generation_status = QuizGenerationStatus.QUEUED
    quiz.generation_error = None
    quiz.needs_review = True
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="quiz.regenerate",
        entity_type="quiz",
        entity_id=quiz.id,
        after={"generation_status": quiz.generation_status.value},
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    await _enqueue(quiz.id)
    return await _detail(session, quiz, topic)


def _clean_options(options: list[str]) -> list[str]:
    if len(options) != 4:
        raise ValidationError("A question must have exactly four options")
    cleaned = [" ".join(option.split()) for option in options]
    if any(not option or len(option) > 300 for option in cleaned):
        raise ValidationError("Options must be non-empty and at most 300 characters")
    if len({option.casefold() for option in cleaned}) != 4:
        raise ValidationError("Options must be distinct")
    return cleaned


async def update_question(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    question_id: uuid.UUID,
    payload: QuestionUpdate,
    ip: str | None = None,
    request_id: str | None = None,
) -> TeacherQuestionOut:
    question, quiz, _topic = await _get_question(session, scope, question_id)
    if quiz.status is not QuizStatus.DRAFT:
        raise ConflictError("Questions can only be edited while the quiz is a draft")

    before = {
        "stem": question.stem,
        "options": list(question.options),
        "correct_option": question.correct_option,
        "approved": question.approved,
    }
    content_changed = False
    if payload.stem is not None:
        stem = " ".join(payload.stem.split())
        if len(stem) < 10 or len(stem) > 500:
            raise ValidationError("Question stems must be between 10 and 500 characters")
        question.stem = stem
        content_changed = True
    if payload.options is not None:
        question.options = _clean_options(payload.options)
        content_changed = True
    if payload.correct_option is not None:
        question.correct_option = payload.correct_option
        content_changed = True
    if payload.explanation is not None:
        question.explanation = " ".join(payload.explanation.split())[:1000]
        content_changed = True
    if payload.difficulty is not None:
        question.difficulty = payload.difficulty
        content_changed = True
    if payload.approved is not None:
        question.approved = payload.approved
    elif content_changed:
        # A teacher edit invalidates the previous approval.
        question.approved = False

    await audit_service.record(
        session,
        actor_id=actor.id,
        action="question.update",
        entity_type="question",
        entity_id=question.id,
        before=before,
        after={
            "stem": question.stem,
            "options": list(question.options),
            "correct_option": question.correct_option,
            "approved": question.approved,
        },
        ip=ip,
        request_id=request_id,
    )
    await session.commit()
    return serialize_question(question)


async def delete_question(
    session: AsyncSession,
    *,
    actor: User,
    scope: CourseScope,
    question_id: uuid.UUID,
    ip: str | None = None,
    request_id: str | None = None,
) -> None:
    question, quiz, _topic = await _get_question(session, scope, question_id)
    if quiz.status is not QuizStatus.DRAFT:
        raise ConflictError("Questions can only be deleted while the quiz is a draft")
    await audit_service.record(
        session,
        actor_id=actor.id,
        action="question.delete",
        entity_type="question",
        entity_id=question.id,
        before={"stem": question.stem, "quiz_id": str(quiz.id)},
        ip=ip,
        request_id=request_id,
    )
    await session.delete(question)
    await session.commit()


async def _enqueue(quiz_id: uuid.UUID) -> bool:
    from app.workers.queue import enqueue_quiz_generation

    try:
        return await enqueue_quiz_generation(quiz_id)
    except Exception:
        logger.exception("quiz_enqueue_failed", quiz_id=str(quiz_id))
        return False


async def _mark_failed(session: AsyncSession, quiz_id: uuid.UUID, error: str) -> None:
    quiz = await quizzes_repo.get_for_update(session, quiz_id)
    if quiz is None:
        return
    quiz.generation_status = QuizGenerationStatus.FAILED
    quiz.generation_error = error[:MAX_GENERATION_ERROR] or "Quiz generation failed"
    quiz.needs_review = True
    await session.commit()
    logger.warning(
        "quiz_generation_failed",
        quiz_id=str(quiz_id),
        error=quiz.generation_error,
    )


async def run_quiz_generation(
    quiz_id: uuid.UUID,
    *,
    generator: Any | None = None,
) -> None:
    """Generate and persist questions for one quiz.

    The worker owns its own session. A failure is recorded on the quiz row so a
    teacher can see it and regenerate instead of the job disappearing silently.
    """
    settings = get_settings()
    async with get_sessionmaker()() as session:
        quiz = await quizzes_repo.get(session, quiz_id)
        if quiz is None:
            logger.warning("quiz_generation_missing_quiz", quiz_id=str(quiz_id))
            return
        if quiz.status is QuizStatus.PUBLISHED:
            return

        auto_approve = quiz.auto_approve and await permission_service.get_permission_service().can(
            session, Role.TEACHER, permission_service.QUIZ_AUTO_APPROVE
        )
        quiz.generation_status = QuizGenerationStatus.GENERATING
        quiz.generation_error = None
        await session.commit()

        topic = await topics_repo.get(session, quiz.topic_id)
        if topic is None:
            await _mark_failed(session, quiz_id, "The topic no longer exists")
            return
        chunks = await topics_repo.list_chunks(session, topic.id)
        source_chunks = [
            QuizSourceChunk(
                id=chunk.id,
                text=chunk.text,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_title=chunk.section_title,
            )
            for chunk in chunks
        ]
        if not source_chunks:
            await _mark_failed(session, quiz_id, "The topic has no source chunks")
            return

        try:
            active_generator = generator or get_quiz_generator()
            result = await active_generator.generate(
                source_chunks,
                settings.quiz_target_questions,
            )
            if len(result.questions) < settings.quiz_min_questions:
                await _mark_failed(
                    session,
                    quiz_id,
                    (
                        f"Only {len(result.questions)} valid questions were generated; "
                        f"{settings.quiz_min_questions} are required"
                    ),
                )
                return

            quiz = await quizzes_repo.get_for_update(session, quiz_id)
            if quiz is None or quiz.status is QuizStatus.PUBLISHED:
                return
            await quizzes_repo.delete_questions(session, quiz.id)
            for index, item in enumerate(result.questions):
                session.add(
                    Question(
                        quiz_id=quiz.id,
                        stem=item.stem,
                        options=list(item.options),
                        correct_option=item.correct_option,
                        explanation=item.explanation,
                        difficulty=item.difficulty
                        if isinstance(item.difficulty, QuestionDifficulty)
                        else QuestionDifficulty(item.difficulty),
                        source_chunk_id=item.source_chunk_id,
                        approved=auto_approve,
                        order_index=index,
                    )
                )
            quiz.generation_status = QuizGenerationStatus.READY
            quiz.generation_error = None
            if auto_approve:
                quiz.status = QuizStatus.PUBLISHED
                quiz.needs_review = False
            else:
                quiz.needs_review = True
            await session.commit()
            logger.info(
                "quiz_generation_complete",
                quiz_id=str(quiz_id),
                questions=len(result.questions),
                rejected=result.rejected,
                rounds=result.rounds,
                auto_approved=auto_approve,
            )
        except Exception as exc:
            await session.rollback()
            await _mark_failed(session, quiz_id, str(exc))
