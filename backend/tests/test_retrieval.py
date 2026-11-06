"""Retrieval, threshold, MMR, and citation-validation tests (Phase 6)."""

from __future__ import annotations

import uuid

from app.core.config import get_settings
from app.core.roles import Role
from app.core.scope import CourseScope
from app.rag.generator import REFUSAL_TEXT, validate_citations
from app.rag.retriever import RetrievedChunk, Retriever, mmr_select
from tests.assistant_utils import store_for_document
from tests.fakes import FakeEmbedder
from tests.topic_utils import seed_document


def _scope(world) -> CourseScope:  # type: ignore[no-untyped-def]
    return CourseScope(
        user_id=world.student.id,
        role=Role.STUDENT,
        course_ids=frozenset({world.course_a.id}),
    )


def _chunk(
    *,
    similarity: float,
    embedding: tuple[float, ...],
    title: str = "chunk",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        vector_id=f"vector-{uuid.uuid4()}",
        document_id=uuid.uuid4(),
        course_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        title="doc.pdf",
        text=f"{title} text",
        page=1,
        section_title=None,
        similarity=similarity,
        embedding=embedding,
    )


def test_mmr_prefers_relevant_then_diverse() -> None:
    query = (1.0, 0.0)
    near_duplicate_a = _chunk(similarity=0.90, embedding=(1.0, 0.0), title="a")
    near_duplicate_b = _chunk(similarity=0.88, embedding=(1.0, 0.0), title="b")
    diverse = _chunk(similarity=0.60, embedding=(0.0, 1.0), title="c")

    selected = mmr_select(
        [near_duplicate_a, near_duplicate_b, diverse],
        query,
        final_k=2,
        lambda_=0.7,
    )

    assert [item.text.split()[0] for item in selected] == ["a", "c"]


async def test_retriever_drops_results_below_similarity_threshold(session, world) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Released", "released": True, "chunk_count": 1}],
    )
    store = store_for_document(seeded, distance=0.90)

    result = await Retriever(
        embedder=FakeEmbedder(),
        vector_store=store,  # type: ignore[arg-type]
        settings=get_settings(),
    ).retrieve(session, question="what is released?", scope=_scope(world))

    assert result.chunks == ()
    assert result.max_similarity == 0.0
    assert store.queries[0]["where"] is not None


async def test_retriever_rechecks_release_state_in_postgres(session, world) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Draft", "released": False, "chunk_count": 1}],
    )
    # Chroma metadata is stale and claims the chunk is released.
    store = store_for_document(seeded, force_released=True)

    result = await Retriever(
        embedder=FakeEmbedder(),
        vector_store=store,  # type: ignore[arg-type]
        settings=get_settings(),
    ).retrieve(session, question="tell me about the draft", scope=_scope(world))

    assert result.chunks == ()
    assert result.refusal is True


async def test_retriever_excludes_other_courses(session, world) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        course_id=world.course_b.id,
        uploaded_by=world.teacher_b.id,
        topic_specs=[{"title": "Other course", "released": True, "chunk_count": 1}],
    )
    store = store_for_document(seeded)

    result = await Retriever(
        embedder=FakeEmbedder(),
        vector_store=store,  # type: ignore[arg-type]
        settings=get_settings(),
    ).retrieve(session, question="what is in the other course?", scope=_scope(world))

    assert result.chunks == ()


async def test_retriever_can_filter_to_one_topic(session, world) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[
            {"title": "Atoms", "released": True, "chunk_count": 1},
            {"title": "Cells", "released": True, "chunk_count": 1},
        ],
    )
    target = seeded.named("Cells")
    store = store_for_document(seeded)

    result = await Retriever(
        embedder=FakeEmbedder(),
        vector_store=store,  # type: ignore[arg-type]
        settings=get_settings(),
    ).retrieve(
        session,
        question="tell me about cells",
        scope=_scope(world),
        topic_id=target.topic.id,
    )

    assert result.chunks
    assert {chunk.topic_id for chunk in result.chunks} == {target.topic.id}


async def test_retriever_caps_selected_chunks_at_final_k(session, world) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Many", "released": True, "chunk_count": 8}],
    )
    store = store_for_document(seeded)

    result = await Retriever(
        embedder=FakeEmbedder(),
        vector_store=store,  # type: ignore[arg-type]
        settings=get_settings(),
    ).retrieve(session, question="many chunks", scope=_scope(world))

    assert len(result.chunks) == get_settings().chroma_final_k


def test_validate_citations_strips_invalid_markers() -> None:
    chunks = [
        _chunk(similarity=1.0, embedding=(1.0, 0.0), title="one"),
        _chunk(similarity=1.0, embedding=(1.0, 0.0), title="two"),
    ]

    answer, citations, refusal = validate_citations("First [1], bogus [9], second [2].", chunks)

    assert refusal is False
    assert "[9]" not in answer
    assert "[1]" in answer and "[2]" in answer
    assert [citation.title for citation in citations] == ["doc.pdf", "doc.pdf"]
    assert len({citation.chunk_id for citation in citations}) == 2


def test_validate_citations_accepts_fullwidth_brackets() -> None:
    chunks = [_chunk(similarity=1.0, embedding=(1.0, 0.0))]

    answer, citations, refusal = validate_citations(
        "Chloroplasts have a double membrane【1】.",
        chunks,
    )

    assert refusal is False
    assert answer == "Chloroplasts have a double membrane[1]."
    assert len(citations) == 1


def test_validate_citations_downgrades_without_a_valid_marker() -> None:
    chunks = [_chunk(similarity=1.0, embedding=(1.0, 0.0))]

    answer, citations, refusal = validate_citations("The model guessed [9].", chunks)

    assert answer == REFUSAL_TEXT
    assert citations == ()
    assert refusal is True
