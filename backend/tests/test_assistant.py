"""Study-assistant success path, streaming contract, and rate limiting."""

from __future__ import annotations

from sqlalchemy import select

from app.core.assistant_ratelimit import get_assistant_limiter
from app.core.config import get_settings
from app.core.roles import Role
from app.core.scope import CourseScope
from app.db.models import RagQueryLog
from app.services import assistant_service
from tests.assistant_utils import (
    FakeAnswerGenerator,
    StubVectorStore,
    assistant_overrides,
    parse_sse,
    store_for_document,
)
from tests.conftest import login
from tests.fakes import FakeEmbedder
from tests.topic_utils import seed_document


def _event(events: list[tuple[str, dict]], name: str) -> dict:  # type: ignore[type-arg]
    return next(data for event, data in events if event == name)


async def test_answer_streams_tokens_then_citations_then_done(client, session, world) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Photosynthesis", "released": True, "chunk_count": 2}],
    )
    await login(client, world.student.email)

    with assistant_overrides(
        embedder=FakeEmbedder(),
        vector_store=store_for_document(seeded),
        generator=FakeAnswerGenerator(raw="The chapter explains photosynthesis clearly [1]."),
    ):
        response = await client.post(
            "/api/v1/student/ask",
            json={"question": "How does photosynthesis work?"},
        )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert [event for event, _ in events][-2:] == ["citations", "done"]
    assert any(event == "token" for event, _ in events)

    citations = _event(events, "citations")
    assert citations["refusal"] is False
    assert len(citations["citations"]) == 1
    citation = citations["citations"][0]
    assert citation["document_id"] == str(seeded.document.id)
    assert citation["title"] == seeded.document.filename
    assert citation["page"] == seeded.topics[0].chunks[0].page_start

    done = _event(events, "done")
    assert done["latency_ms"] >= 0

    log = (await session.execute(select(RagQueryLog))).scalar_one()
    assert log.refusal is False
    assert log.tokens_in > 0
    assert log.tokens_out > 0
    assert log.max_similarity == 1.0


async def test_cross_chapter_answer_cites_both_documents(client, session, world) -> None:  # type: ignore[no-untyped-def]
    first = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Atoms", "released": True, "chunk_count": 1}],
    )
    second = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Cells", "released": True, "chunk_count": 1}],
    )
    store = store_for_document(first)
    for vector_id, record in store_for_document(second).records.items():
        store.records[vector_id] = record
    await login(client, world.student.email)

    with assistant_overrides(
        embedder=FakeEmbedder(),
        vector_store=store,
        generator=FakeAnswerGenerator(raw="Atoms differ from cells [1][2]."),
    ):
        response = await client.post(
            "/api/v1/student/ask",
            json={"question": "Compare atoms and cells"},
        )

    assert response.status_code == 200, response.text
    citations = _event(parse_sse(response.text), "citations")
    assert citations["refusal"] is False
    assert {item["document_id"] for item in citations["citations"]} == {
        str(first.document.id),
        str(second.document.id),
    }


async def test_rate_limit_returns_429_before_streaming(client, session, world) -> None:  # type: ignore[no-untyped-def]
    limiter = get_assistant_limiter()
    await limiter.reset()
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Unreleased", "released": False, "chunk_count": 1}],
    )
    await login(client, world.student.email)

    try:
        with assistant_overrides(
            embedder=FakeEmbedder(),
            vector_store=store_for_document(seeded),
            generator=FakeAnswerGenerator(),
        ):
            for _ in range(limiter.limit):
                allowed = await client.post(
                    "/api/v1/student/ask",
                    json={"question": "Anything from released material?"},
                )
                assert allowed.status_code == 200, allowed.text

            blocked = await client.post(
                "/api/v1/student/ask",
                json={"question": "One more question?"},
            )
    finally:
        await limiter.reset()

    assert blocked.status_code == 429
    assert blocked.headers["retry-after"]
    assert blocked.json()["status"] == 429


async def test_debug_flag_is_the_only_way_raw_questions_are_stored(session, world) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Released", "released": True, "chunk_count": 1}],
    )
    question = "Store this question only in debug mode"
    settings = get_settings().model_copy(update={"rag_store_questions": True})
    scope = CourseScope(
        user_id=world.student.id,
        role=Role.STUDENT,
        course_ids=frozenset({world.course_a.id}),
    )

    result = await assistant_service.ask(
        session,
        scope=scope,
        question=question,
        course_id=None,
        topic_id=None,
        settings=settings,
        embedder=FakeEmbedder(),
        vector_store=store_for_document(seeded),  # type: ignore[arg-type]
        generator=FakeAnswerGenerator(raw="Answer [1]."),
    )

    assert result.refusal is False
    log = (await session.execute(select(RagQueryLog))).scalar_one()
    assert log.question_text == question


async def test_question_validation_runs_before_streaming(client, world) -> None:  # type: ignore[no-untyped-def]
    await login(client, world.student.email)

    with assistant_overrides(
        embedder=FakeEmbedder(),
        vector_store=StubVectorStore(),
        generator=FakeAnswerGenerator(),
    ):
        response = await client.post("/api/v1/student/ask", json={"question": "   "})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
