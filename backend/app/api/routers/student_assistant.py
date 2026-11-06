"""Student study-assistant SSE endpoint.

All guard work (auth, rate limit, scope, question validation, retrieval,
generation, logging) happens before the first byte. Once the stream starts it
always ends with a ``citations`` event and a ``done`` event, including on a
generation failure, which emits a preceding ``error`` event.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_course_scope, get_current_user, require_role
from app.api.schemas.assistant import AskRequest, CitationOut
from app.core.assistant_ratelimit import AssistantRateLimiter, get_assistant_limiter
from app.core.config import Settings, get_settings
from app.core.errors import ForbiddenError, TooManyRequestsError
from app.core.roles import Role
from app.core.scope import CourseScope
from app.db.models import User
from app.db.session import get_session
from app.rag.embedder import TextEmbedder, get_embedder
from app.rag.generator import AnswerGenerator, get_answer_generator
from app.rag.vector_store import VectorStore, get_vector_store
from app.services import assistant_service, permission_service

router = APIRouter(
    prefix="/student",
    tags=["student-assistant"],
    dependencies=[Depends(require_role(Role.STUDENT))],
)

ERROR_MESSAGE = "The study assistant is temporarily unavailable. Please try again."


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _token_chunks(text: str, size: int = 48) -> list[str]:
    """Split text on whitespace boundaries so SSE token events look natural."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary + 1
        chunks.append(text[start:end])
        start = end
    return chunks


async def _event_stream(result: assistant_service.AssistantAnswer) -> AsyncIterator[str]:
    if result.error_code:
        yield _sse("error", {"code": result.error_code, "message": ERROR_MESSAGE})
    else:
        for chunk in _token_chunks(result.answer):
            yield _sse("token", {"text": chunk})

    citations = [
        CitationOut(
            document_id=citation.document_id,
            title=citation.title,
            page=citation.page,
        ).model_dump(mode="json")
        for citation in result.citations
    ]
    yield _sse("citations", {"citations": citations, "refusal": result.refusal})
    yield _sse("done", {"latency_ms": result.latency_ms})


@router.post("/ask")
async def ask_study_assistant(
    payload: AskRequest,
    user: User = Depends(get_current_user),
    scope: CourseScope = Depends(get_course_scope),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    embedder: TextEmbedder = Depends(get_embedder),
    vector_store: VectorStore = Depends(get_vector_store),
    generator: AnswerGenerator = Depends(get_answer_generator),
    limiter: AssistantRateLimiter = Depends(get_assistant_limiter),
) -> StreamingResponse:
    capability_enabled = await permission_service.get_permission_service().can(
        session, Role.STUDENT, permission_service.ASSISTANT_ENABLED
    )
    if not capability_enabled:
        raise ForbiddenError("The study assistant is disabled for students")

    allowed, retry_after = await limiter.allow(str(user.id))
    if not allowed:
        raise TooManyRequestsError(
            "You have reached the study assistant limit. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    result = await assistant_service.ask(
        session,
        scope=scope,
        question=payload.question,
        course_id=payload.course_id,
        topic_id=payload.topic_id,
        settings=settings,
        embedder=embedder,
        vector_store=vector_store,
        generator=generator,
    )

    return StreamingResponse(
        _event_stream(result),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
