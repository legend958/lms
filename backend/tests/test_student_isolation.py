"""Student isolation guarantees for Phase 4.

Cross-scope resources must look non-existent (404, never 403), student payloads
must not leak release or vector metadata, and notifications are private.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import NotificationType
from app.db.repositories import courses as courses_repo
from app.db.repositories import notifications as notifications_repo
from tests.conftest import login
from tests.topic_utils import seed_document, seed_published_task

FORBIDDEN_KEYS = {
    "release_state",
    "released_by",
    "released_at",
    "vector_id",
    "chunk_id",
    "correct_option",
    "chunks",
}


def _all_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(key)
            keys |= _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            keys |= _all_keys(child)
    return keys


async def _seed_courses(session: AsyncSession, world) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    await courses_repo.enroll(session, world.course_b.id, world.student_b.id)
    await session.commit()

    seeded_a = await seed_document(
        session,
        world,
        topic_specs=[{"title": "A Task", "chunk_count": 1, "released": True}],
    )
    seeded_b = await seed_document(
        session,
        world,
        course_id=world.course_b.id,
        uploaded_by=world.teacher_b.id,
        topic_specs=[{"title": "B Task", "chunk_count": 1, "released": True}],
    )
    task_a = await seed_published_task(session, seeded_a.named("A Task").topic)
    task_b = await seed_published_task(session, seeded_b.named("B Task").topic)
    return {
        "seeded_a": seeded_a,
        "seeded_b": seeded_b,
        "task_a": task_a,
        "task_b": task_b,
    }


async def test_student_payload_has_no_release_or_vector_metadata(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    await _seed_courses(session, world)
    student = await client_factory()
    await login(student, world.student.email)

    tasks = await student.get("/api/v1/student/tasks")
    dashboard = await student.get("/api/v1/student/dashboard")
    assert tasks.status_code == 200, tasks.text
    assert dashboard.status_code == 200, dashboard.text

    for response in (tasks, dashboard):
        keys = _all_keys(response.json())
        assert keys.isdisjoint(FORBIDDEN_KEYS), keys & FORBIDDEN_KEYS


async def test_students_cannot_touch_tasks_outside_their_enrollments(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    data = await _seed_courses(session, world)

    student_a = await client_factory()
    await login(student_a, world.student.email)
    student_b = await client_factory()
    await login(student_b, world.student_b.email)

    a_on_b = await student_a.patch(
        f"/api/v1/student/tasks/{data['task_b'].id}",
        json={"status": "completed"},
    )
    assert a_on_b.status_code == 404

    b_on_a = await student_b.patch(
        f"/api/v1/student/tasks/{data['task_a'].id}",
        json={"status": "completed"},
    )
    assert b_on_a.status_code == 404

    list_a = await student_a.get("/api/v1/student/tasks")
    assert [item["title"] for item in list_a.json()["items"]] == ["A Task"]
    list_b = await student_b.get("/api/v1/student/tasks")
    assert [item["title"] for item in list_b.json()["items"]] == ["B Task"]

    foreign_filter = await student_a.get(f"/api/v1/student/tasks?course_id={world.course_b.id}")
    assert foreign_filter.status_code == 404


async def test_draft_topic_task_never_reaches_students(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Hidden Draft", "chunk_count": 1}],
    )
    draft_task = await seed_published_task(session, seeded.named("Hidden Draft").topic)

    student = await client_factory()
    await login(student, world.student.email)

    listed = await student.get("/api/v1/student/tasks")
    assert listed.json()["items"] == []
    assert "Hidden Draft" not in listed.text

    dashboard = await student.get("/api/v1/student/dashboard")
    assert dashboard.json()["due_soon"] == []

    patched = await student.patch(
        f"/api/v1/student/tasks/{draft_task.id}",
        json={"status": "completed"},
    )
    assert patched.status_code == 404


async def test_teacher_cannot_use_student_task_endpoints(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    data = await _seed_courses(session, world)
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    assert (await teacher.get("/api/v1/student/tasks")).status_code == 403
    assert (await teacher.get("/api/v1/student/dashboard")).status_code == 403
    patched = await teacher.patch(
        f"/api/v1/student/tasks/{data['task_a'].id}",
        json={"status": "completed"},
    )
    assert patched.status_code == 403


async def test_notifications_are_private_to_the_recipient(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    notification = await notifications_repo.create(
        session,
        user_id=world.student.id,
        notification_type=NotificationType.TASK_RELEASED,
        payload={
            "task_id": str(uuid.uuid4()),
            "title": "Private",
            "due_at": datetime.now(UTC).isoformat(),
        },
    )
    await session.commit()

    owner = await client_factory()
    await login(owner, world.student.email)
    inbox = await owner.get("/api/v1/notifications")
    assert inbox.json()["unread_count"] == 1
    assert inbox.json()["items"][0]["payload"]["title"] == "Private"

    outsider = await client_factory()
    await login(outsider, world.student_b.email)
    assert (await outsider.get("/api/v1/notifications")).json() == {
        "items": [],
        "unread_count": 0,
    }
    stolen = await outsider.post(f"/api/v1/notifications/{notification.id}/read")
    assert stolen.status_code == 404

    still_unread = await owner.get("/api/v1/notifications")
    assert still_unread.json()["unread_count"] == 1
