"""Blocking release-gate tests.

The gate must hold independently in the student DTO routes, the SQL repository,
the service guard, and the vector-store filter. A draft topic must never appear
in a student response, even when it shares a course with released content.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.roles import Role, TaskStatus, TopicReleaseState
from app.core.scope import CourseScope
from app.db.models import Task
from app.db.repositories import topics as topics_repo
from app.main import app
from app.rag.access import student_vector_where
from app.rag.vector_store import VectorStoreError, get_vector_store
from app.services import topic_service
from app.services.topic_service import assert_topic_released
from tests.conftest import login
from tests.fakes import FakeVectorStore
from tests.topic_utils import fake_vector_store, seed_document

DUE_AT = "2026-12-01T23:59:59Z"


def student_scope(world) -> CourseScope:  # type: ignore[no-untyped-def]
    return CourseScope(
        user_id=world.student.id,
        role=Role.STUDENT,
        course_ids=frozenset({world.course_a.id}),
    )


@dataclass
class SeededGate:
    released_title: str
    draft_title: str


async def _seed_gate(session: AsyncSession, world) -> tuple[object, SeededGate]:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[
            {"title": "Released Topic", "chunk_count": 2, "released": True},
            {"title": "Draft Topic", "chunk_count": 2},
        ],
    )
    return seeded, SeededGate(released_title="Released Topic", draft_title="Draft Topic")


async def test_student_surfaces_and_sql_exclude_drafts(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded, _ = await _seed_gate(session, world)
    student = await client_factory()
    await login(student, world.student.email)

    response = await student.get("/api/v1/student/topics")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["title"] for row in rows] == ["Released Topic"]
    assert "Draft Topic" not in response.text

    draft = seeded.named("Draft Topic").topic
    hidden = await student.get(f"/api/v1/student/topics/{draft.id}")
    assert hidden.status_code == 404

    scope = student_scope(world)
    sql_topics = await topics_repo.list_released_for_scope(session, scope)
    assert [topic.title for topic in sql_topics] == ["Released Topic"]

    sql_chunks = await topics_repo.list_released_chunks_for_scope(session, scope)
    draft_chunk_ids = {chunk.id for chunk in seeded.named("Draft Topic").chunks}
    assert sql_chunks
    assert not (draft_chunk_ids & {chunk.id for chunk in sql_chunks})


async def test_vector_filter_excludes_drafts_and_tracks_release(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded, _ = await _seed_gate(session, world)
    store = fake_vector_store(seeded)
    app.dependency_overrides[get_vector_store] = lambda: store

    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    draft = seeded.named("Draft Topic")
    before = store.query(
        [0.0, 0.0],
        where=student_vector_where([world.course_a.id]),
        top_k=20,
    )
    visible_before = set(before["ids"][0])
    assert visible_before == {chunk.vector_id for chunk in seeded.named("Released Topic").chunks}
    assert not (visible_before & {chunk.vector_id for chunk in draft.chunks})

    released = await teacher.post(
        f"/api/v1/topics/{draft.topic.id}/release",
        json={"week_no": 2, "due_at": DUE_AT},
    )
    assert released.status_code == 200, released.text
    assert all(store.records[chunk.vector_id]["metadata"]["released"] for chunk in draft.chunks)

    student = await client_factory()
    await login(student, world.student.email)
    visible = await student.get("/api/v1/student/topics")
    assert {row["title"] for row in visible.json()} == {"Released Topic", "Draft Topic"}

    unreleased = await teacher.post(f"/api/v1/topics/{draft.topic.id}/unrelease")
    assert unreleased.status_code == 200, unreleased.text
    assert not any(store.records[chunk.vector_id]["metadata"]["released"] for chunk in draft.chunks)

    hidden = await student.get(f"/api/v1/student/topics/{draft.topic.id}")
    assert hidden.status_code == 404
    after = await student.get("/api/v1/student/topics")
    assert [row["title"] for row in after.json()] == ["Released Topic"]


async def test_release_commits_when_chroma_fails_and_queues_reconcile(
    client_factory, world, session: AsyncSession, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Resilient Topic", "chunk_count": 1}],
    )

    class FailingStore(FakeVectorStore):
        def update_metadata(self, **kwargs: object) -> None:
            del kwargs
            raise VectorStoreError("Chroma is down")

    store = FailingStore()
    app.dependency_overrides[get_vector_store] = lambda: store
    queued: list[bool] = []

    async def fake_enqueue() -> bool:
        queued.append(True)
        return True

    monkeypatch.setattr(topic_service, "enqueue_reconcile", fake_enqueue)
    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    topic = seeded.named("Resilient Topic").topic

    response = await teacher.post(
        f"/api/v1/topics/{topic.id}/release",
        json={"week_no": 1, "due_at": DUE_AT},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["chroma_synced"] is False
    assert payload["reconcile_queued"] is True
    assert queued == [True]

    session.expire_all()
    await session.refresh(topic)
    assert topic.release_state is TopicReleaseState.RELEASED
    chunks = await topics_repo.list_chunks(session, topic.id)
    assert all(chunk.released for chunk in chunks)
    task = await session.get(Task, uuid.UUID(payload["task_id"]))
    assert task is not None
    assert task.status is TaskStatus.PUBLISHED


async def test_unrelease_fails_closed_when_chroma_fails(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Locked Topic", "chunk_count": 1, "released": True}],
    )
    topic = seeded.named("Locked Topic").topic
    good_store = fake_vector_store(seeded)

    class FailingStore(FakeVectorStore):
        def update_metadata(self, **kwargs: object) -> None:
            del kwargs
            raise VectorStoreError("Chroma is down")

    app.dependency_overrides[get_vector_store] = lambda: FailingStore()
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    failed = await teacher.post(f"/api/v1/topics/{topic.id}/unrelease")
    assert failed.status_code == 503, failed.text
    await session.refresh(topic)
    assert topic.release_state is TopicReleaseState.RELEASED
    chunks = await topics_repo.list_chunks(session, topic.id)
    assert all(chunk.released for chunk in chunks)

    app.dependency_overrides[get_vector_store] = lambda: good_store
    succeeded = await teacher.post(f"/api/v1/topics/{topic.id}/unrelease")
    assert succeeded.status_code == 200, succeeded.text
    assert succeeded.json()["release_state"] == "draft"
    session.expire_all()
    await session.refresh(topic)
    assert topic.release_state is TopicReleaseState.DRAFT
    chunks = await topics_repo.list_chunks(session, topic.id)
    assert not any(chunk.released for chunk in chunks)
    assert not any(good_store.records[chunk.vector_id]["metadata"]["released"] for chunk in chunks)


async def test_document_delete_is_blocked_while_topics_are_released(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded, _ = await _seed_gate(session, world)
    released = seeded.named("Released Topic").topic
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    blocked = await teacher.delete(f"/api/v1/documents/{seeded.document.id}")
    assert blocked.status_code == 409, blocked.text

    unreleased = await teacher.post(f"/api/v1/topics/{released.id}/unrelease")
    assert unreleased.status_code == 200, unreleased.text

    removed = await teacher.delete(f"/api/v1/documents/{seeded.document.id}")
    assert removed.status_code == 204, removed.text

    student = await client_factory()
    await login(student, world.student.email)
    after = await student.get("/api/v1/student/topics")
    assert after.json() == []


async def test_service_gate_rejects_draft_topic(world, session: AsyncSession) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Still Draft", "chunk_count": 1}],
    )
    draft = seeded.named("Still Draft").topic
    with pytest.raises(NotFoundError):
        assert_topic_released(draft)
