from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.roles import TaskStatus, TopicSource
from app.db.models import Task
from app.db.repositories import topics as topics_repo
from app.rag.topics import (
    GroqTopicExtractor,
    TopicChunk,
    TopicExtractionUnavailable,
    TopicProposal,
    TopicSchemaError,
    _normalise_proposals,
    fallback_topics,
)
from app.services.topic_service import assert_topic_released
from tests.conftest import login
from tests.topic_utils import seed_document

DUE_AT = "2026-12-01T23:59:59Z"


@pytest.fixture
async def seeded(world, session: AsyncSession):  # type: ignore[no-untyped-def]
    return await seed_document(
        session,
        world,
        topic_specs=[
            {"title": "Atoms", "chunk_count": 2},
            {"title": "Chemical Bonds", "chunk_count": 2},
        ],
    )


async def test_topic_list_and_release_round_trip(
    client_factory, world, session: AsyncSession, seeded
) -> None:  # type: ignore[no-untyped-def]
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    listing = await teacher.get(f"/api/v1/documents/{seeded.document.id}/topics")
    assert listing.status_code == 200, listing.text
    payload = listing.json()
    assert [row["title"] for row in payload] == ["Atoms", "Chemical Bonds"]
    assert payload[0]["release_state"] == "draft"
    assert payload[0]["chunk_count"] == 2
    assert payload[0]["task_id"] is None

    student = await client_factory()
    await login(student, world.student.email)
    empty = await student.get("/api/v1/student/topics")
    assert empty.status_code == 200
    assert empty.json() == []

    atoms = seeded.named("Atoms").topic
    released = await teacher.post(
        f"/api/v1/topics/{atoms.id}/release",
        json={"week_no": 3, "due_at": DUE_AT, "generate_quiz": False},
    )
    assert released.status_code == 200, released.text
    result = released.json()
    assert result["task_status"] == "published"
    assert result["chroma_synced"] is True
    assert result["task_id"]

    visible = await student.get("/api/v1/student/topics")
    assert visible.status_code == 200, visible.text
    rows = visible.json()
    assert [row["title"] for row in rows] == ["Atoms"]
    assert "release_state" not in rows[0]
    assert "released_at" not in rows[0]
    assert "vector_id" not in rows[0]

    detail = await student.get(f"/api/v1/student/topics/{atoms.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert len(body["chunks"]) == 2
    assert set(body["chunks"][0]) == {"id", "text", "page_start", "page_end", "section_title"}

    draft = seeded.named("Chemical Bonds").topic
    hidden = await student.get(f"/api/v1/student/topics/{draft.id}")
    assert hidden.status_code == 404

    renamed = await teacher.patch(
        f"/api/v1/topics/{atoms.id}",
        json={"title": "Atoms and Matter", "summary": "Updated copy"},
    )
    assert renamed.status_code == 200, renamed.text
    task = (await session.execute(select(Task).where(Task.topic_id == atoms.id))).scalar_one()
    assert task.title == "Atoms and Matter"
    assert task.description == "Updated copy"

    blocked = await teacher.patch(
        f"/api/v1/topics/{atoms.id}",
        json={"page_start": 2, "page_end": 3},
    )
    assert blocked.status_code == 409

    unreleased = await teacher.post(f"/api/v1/topics/{atoms.id}/unrelease")
    assert unreleased.status_code == 200, unreleased.text
    assert unreleased.json()["release_state"] == "draft"
    await session.refresh(task)
    assert task.status is TaskStatus.ARCHIVED

    hidden_again = await student.get("/api/v1/student/topics")
    assert hidden_again.json() == []

    rereleased = await teacher.post(
        f"/api/v1/topics/{atoms.id}/release",
        json={"week_no": 4, "due_at": DUE_AT, "generate_quiz": False},
    )
    assert rereleased.status_code == 200, rereleased.text
    assert rereleased.json()["task_id"] == result["task_id"]


async def test_split_and_merge_preserve_chunks(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Whole Chapter", "chunk_count": 4}],
    )
    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    original = seeded.named("Whole Chapter").topic
    chunks = seeded.named("Whole Chapter").chunks

    split = await teacher.post(
        f"/api/v1/topics/{original.id}/split",
        json={"at_chunk_id": str(chunks[2].id), "new_title": "Second Half"},
    )
    assert split.status_code == 200, split.text
    created = split.json()
    assert created["chunk_count"] == 2
    assert created["page_start"] == 3
    assert created["page_end"] == 4
    assert created["release_state"] == "draft"

    current = await teacher.get(f"/api/v1/documents/{seeded.document.id}/topics")
    assert [row["title"] for row in current.json()] == ["Whole Chapter", "Second Half"]
    first = current.json()[0]
    assert first["chunk_count"] == 2
    assert first["page_start"] == 1
    assert first["page_end"] == 2

    merged = await teacher.post(
        "/api/v1/topics/merge",
        json={
            "topic_ids": [first["id"], created["id"]],
            "title": "Whole Chapter Again",
        },
    )
    assert merged.status_code == 200, merged.text
    combined = merged.json()
    assert combined["chunk_count"] == 4
    assert combined["page_start"] == 1
    assert combined["page_end"] == 4

    after = await teacher.get(f"/api/v1/documents/{seeded.document.id}/topics")
    assert [row["title"] for row in after.json()] == ["Whole Chapter Again"]

    document = await teacher.get(f"/api/v1/documents/{seeded.document.id}")
    assert document.status_code == 200, document.text
    assert document.json()["topic_count"] == 1

    refused = await teacher.delete(f"/api/v1/topics/{combined['id']}")
    assert refused.status_code == 409


async def test_split_rejects_first_chunk_and_bad_title(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Only Topic", "chunk_count": 2}],
    )
    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    topic = seeded.named("Only Topic").topic
    chunks = seeded.named("Only Topic").chunks

    first = await teacher.post(
        f"/api/v1/topics/{topic.id}/split",
        json={"at_chunk_id": str(chunks[0].id), "new_title": "Nope"},
    )
    assert first.status_code == 422

    duplicate = await teacher.post(
        f"/api/v1/topics/{topic.id}/split",
        json={"at_chunk_id": str(chunks[1].id), "new_title": "Only Topic"},
    )
    assert duplicate.status_code == 409


async def test_topic_permissions_are_course_scoped(client_factory, world, seeded) -> None:  # type: ignore[no-untyped-def]
    teacher_b = await client_factory()
    await login(teacher_b, world.teacher_b.email)

    listing = await teacher_b.get(f"/api/v1/documents/{seeded.document.id}/topics")
    assert listing.status_code == 404
    atoms = seeded.named("Atoms").topic
    release = await teacher_b.post(
        f"/api/v1/topics/{atoms.id}/release",
        json={"week_no": 1, "due_at": DUE_AT, "generate_quiz": False},
    )
    assert release.status_code == 404

    student = await client_factory()
    await login(student, world.student.email)
    patch = await student.patch(
        f"/api/v1/topics/{atoms.id}",
        json={"title": "Student edit"},
    )
    assert patch.status_code == 403


async def test_release_requires_ready_document(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    from app.core.roles import DocumentStatus

    seeded = await seed_document(
        session,
        world,
        status=DocumentStatus.TOPIC_EXTRACTION,
        topic_specs=[{"title": "Pending", "chunk_count": 1}],
    )
    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    response = await teacher.post(
        f"/api/v1/topics/{seeded.named('Pending').topic.id}/release",
        json={"week_no": 1, "due_at": DUE_AT, "generate_quiz": False},
    )
    assert response.status_code == 409


async def test_unrelease_cannot_show_a_draft(
    client_factory, world, session: AsyncSession, seeded
) -> None:  # type: ignore[no-untyped-def]
    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    atoms = seeded.named("Atoms").topic
    await teacher.post(
        f"/api/v1/topics/{atoms.id}/release",
        json={"week_no": 2, "due_at": DUE_AT},
    )
    await teacher.post(f"/api/v1/topics/{atoms.id}/unrelease")

    stored = await topics_repo.get(session, atoms.id)
    assert stored is not None
    await session.refresh(stored)
    with pytest.raises(NotFoundError):
        assert_topic_released(stored)

    student = await client_factory()
    await login(student, world.student.email)
    detail = await student.get(f"/api/v1/student/topics/{atoms.id}")
    assert detail.status_code == 404


async def test_topic_extraction_validates_and_falls_back() -> None:
    chunk_id = uuid.uuid4()
    chunks = [
        TopicChunk(
            id=chunk_id,
            chunk_index=0,
            text="Atoms combine.",
            page_start=1,
            page_end=1,
            section_title="Atoms",
        )
    ]

    with pytest.raises(TopicSchemaError):
        _normalise_proposals(
            [
                TopicProposal(
                    title="Bad page range",
                    summary="",
                    page_start=1,
                    page_end=99,
                    chunk_ids=(chunk_id,),
                )
            ],
            chunks,
            4,
            source=TopicSource.LLM,
        )

    with pytest.raises(TopicSchemaError):
        _normalise_proposals(
            [
                TopicProposal(
                    title="Unknown chunk",
                    summary="",
                    page_start=1,
                    page_end=1,
                    chunk_ids=(uuid.uuid4(),),
                )
            ],
            chunks,
            4,
            source=TopicSource.LLM,
        )

    with pytest.raises(TopicSchemaError):
        _normalise_proposals(
            [
                TopicProposal(
                    title="Duplicate",
                    summary="",
                    page_start=1,
                    page_end=1,
                    chunk_ids=(chunk_id,),
                ),
                TopicProposal(
                    title="Duplicate",
                    summary="",
                    page_start=1,
                    page_end=1,
                    chunk_ids=(chunk_id,),
                ),
            ],
            chunks,
            4,
            source=TopicSource.LLM,
        )

    fallback = fallback_topics(chunks, 1)
    assert fallback.source is TopicSource.FALLBACK
    assert fallback.topics[0].needs_review is True
    assert fallback.topics[0].chunk_ids == (chunk_id,)


async def test_groq_extractor_without_key_is_unavailable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "groq_api_key", "")
    extractor = GroqTopicExtractor(get_settings())
    chunk = TopicChunk(
        id=uuid.uuid4(),
        chunk_index=0,
        text="Atoms combine.",
        page_start=1,
        page_end=1,
        section_title="Atoms",
    )
    with pytest.raises(TopicExtractionUnavailable):
        await extractor.extract([chunk], 1)
