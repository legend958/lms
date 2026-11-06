"""Study-assistant orchestration: validate, retrieve, generate, log.

The service is the only place that decides whether a student sees an answer.
Retrieval is release-gated twice (Chroma filter plus a Postgres re-check in the
repository). If nothing survives, or the generated answer has no valid
citation, the result is the fixed refusal and zero citations. Query logging is
written before the SSE response starts, so a client disconnect cannot lose it.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationError
from app.core.roles import DocumentStatus, TopicReleaseState
from app.core.scope import CourseScope
from app.db.repositories import rag as rag_repo
from app.rag.embedder import TextEmbedder
from app.rag.generator import REFUSAL_TEXT, AnswerGenerator, Citation
from app.rag.retriever import RetrievedChunk, Retriever
from app.rag.vector_store import VectorStore

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AssistantAnswer:
    answer: str
    citations: tuple[Citation, ...]
    refusal: bool
    latency_ms: int
    max_similarity: float
    injection_flag: bool
    tokens_in: int
    tokens_out: int
    error_code: str | None = None


def _normalize_question(question: str) -> str:
    return " ".join(question.split())


def _question_hash(question: str) -> str:
    return hashlib.sha256(_normalize_question(question).encode("utf-8")).hexdigest()


def _refusal(latency_ms: int, *, injection_flag: bool = False) -> AssistantAnswer:
    return AssistantAnswer(
        answer=REFUSAL_TEXT,
        citations=(),
        refusal=True,
        latency_ms=latency_ms,
        max_similarity=0.0,
        injection_flag=injection_flag,
        tokens_in=0,
        tokens_out=0,
    )


def _error(latency_ms: int, *, error_code: str) -> AssistantAnswer:
    return AssistantAnswer(
        answer="",
        citations=(),
        refusal=True,
        latency_ms=latency_ms,
        max_similarity=0.0,
        injection_flag=False,
        tokens_in=0,
        tokens_out=0,
        error_code=error_code,
    )


def _log_course_id(
    course_id: uuid.UUID | None,
    chunks: tuple[RetrievedChunk, ...],
) -> uuid.UUID | None:
    if course_id is not None:
        return course_id
    courses = {chunk.course_id for chunk in chunks}
    if len(courses) == 1:
        return next(iter(courses))
    return None


async def _log(
    session: AsyncSession,
    *,
    settings: Settings,
    user_id: uuid.UUID,
    course_id: uuid.UUID | None,
    question: str,
    chunks: tuple[RetrievedChunk, ...],
    max_similarity: float,
    answer: AssistantAnswer,
) -> None:
    values: dict[str, object] = {
        "user_id": user_id,
        "course_id": _log_course_id(course_id, chunks),
        "question_hash": _question_hash(question),
        "question_text": question if settings.rag_store_questions else None,
        "chunk_ids": [str(chunk.chunk_id) for chunk in chunks],
        "max_similarity": max_similarity,
        "refusal": answer.refusal,
        "injection_flag": answer.injection_flag,
        "error_code": answer.error_code,
        "latency_ms": answer.latency_ms,
        "tokens_in": answer.tokens_in,
        "tokens_out": answer.tokens_out,
        "prompt_version": settings.rag_prompt_version,
    }
    try:
        await rag_repo.log_query(session, **values)
    except Exception as exc:  # pragma: no cover - logging must not break answers
        logger.error("assistant_log_failed", error=str(exc))
        await session.rollback()


async def _validate_topic(
    session: AsyncSession,
    *,
    scope: CourseScope,
    course_id: uuid.UUID | None,
    topic_id: uuid.UUID,
) -> tuple[bool, uuid.UUID | None]:
    """Return ``(visible_for_retrieval, topic_course_id)``.

    A topic outside the caller's scope raises 404. An in-scope topic that is
    draft, deleted, or attached to a non-ready document is treated as having no
    released material (refusal), not as a server error.
    """
    row = await rag_repo.get_topic_context(session, topic_id=topic_id)
    if row is None:
        raise NotFoundError("Topic not found")
    topic, document = row
    if topic.course_id not in scope.course_ids:
        raise NotFoundError("Topic not found")
    if course_id is not None and topic.course_id != course_id:
        raise NotFoundError("Topic not found")
    if (
        topic.release_state is not TopicReleaseState.RELEASED
        or document.status is not DocumentStatus.READY
    ):
        return False, topic.course_id
    return True, topic.course_id


async def ask(
    session: AsyncSession,
    *,
    scope: CourseScope,
    question: str,
    course_id: uuid.UUID | None,
    topic_id: uuid.UUID | None,
    settings: Settings,
    embedder: TextEmbedder,
    vector_store: VectorStore,
    generator: AnswerGenerator,
) -> AssistantAnswer:
    normalized = _normalize_question(question)
    if not normalized:
        raise ValidationError("Question must not be empty")
    if len(normalized) > settings.rag_max_question_chars:
        raise ValidationError(
            f"Question must be at most {settings.rag_max_question_chars} characters"
        )
    if course_id is not None:
        scope.require(course_id)

    started = time.perf_counter()

    def elapsed_ms() -> int:
        return max(0, int((time.perf_counter() - started) * 1000))

    if topic_id is not None:
        visible, topic_course_id = await _validate_topic(
            session,
            scope=scope,
            course_id=course_id,
            topic_id=topic_id,
        )
        if not visible:
            answer = _refusal(elapsed_ms())
            await _log(
                session,
                settings=settings,
                user_id=scope.user_id,
                course_id=course_id or topic_course_id,
                question=normalized,
                chunks=(),
                max_similarity=0.0,
                answer=answer,
            )
            return answer

    retriever = Retriever(embedder=embedder, vector_store=vector_store, settings=settings)
    try:
        retrieval = await retriever.retrieve(
            session,
            question=normalized,
            scope=scope,
            course_id=course_id,
            topic_id=topic_id,
        )
    except Exception as exc:
        logger.error("assistant_retrieval_failed", error=str(exc))
        await session.rollback()
        answer = _error(elapsed_ms(), error_code="retrieval_failed")
        await _log(
            session,
            settings=settings,
            user_id=scope.user_id,
            course_id=course_id,
            question=normalized,
            chunks=(),
            max_similarity=0.0,
            answer=answer,
        )
        return answer

    if not retrieval.chunks:
        answer = _refusal(elapsed_ms())
        await _log(
            session,
            settings=settings,
            user_id=scope.user_id,
            course_id=course_id,
            question=normalized,
            chunks=(),
            max_similarity=retrieval.max_similarity,
            answer=answer,
        )
        return answer

    try:
        generated = await generator.answer(normalized, retrieval.chunks)
    except Exception as exc:
        logger.error("assistant_generation_failed", error=str(exc))
        answer = _error(elapsed_ms(), error_code="generation_failed")
        await _log(
            session,
            settings=settings,
            user_id=scope.user_id,
            course_id=course_id,
            question=normalized,
            chunks=retrieval.chunks,
            max_similarity=retrieval.max_similarity,
            answer=answer,
        )
        return answer

    if generated.refusal:
        answer = AssistantAnswer(
            answer=REFUSAL_TEXT,
            citations=(),
            refusal=True,
            latency_ms=elapsed_ms(),
            max_similarity=retrieval.max_similarity,
            injection_flag=generated.injection_flag,
            tokens_in=generated.tokens_in,
            tokens_out=generated.tokens_out,
        )
    else:
        answer = AssistantAnswer(
            answer=generated.answer,
            citations=generated.citations,
            refusal=False,
            latency_ms=elapsed_ms(),
            max_similarity=retrieval.max_similarity,
            injection_flag=generated.injection_flag,
            tokens_in=generated.tokens_in,
            tokens_out=generated.tokens_out,
        )

    await _log(
        session,
        settings=settings,
        user_id=scope.user_id,
        course_id=course_id,
        question=normalized,
        chunks=retrieval.chunks,
        max_similarity=retrieval.max_similarity,
        answer=answer,
    )
    return answer
