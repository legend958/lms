"""Student task list, completion, dashboard, and notification fan-out."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import TaskProgressStatus
from app.db.models import Announcement, Course, TaskProgress
from app.db.repositories import courses as courses_repo
from app.db.repositories import tasks as tasks_repo
from app.main import app
from app.rag.vector_store import get_vector_store
from tests.conftest import login
from tests.topic_utils import fake_vector_store, seed_document, seed_published_task

FUTURE = datetime(2026, 12, 10, 23, 59, tzinfo=UTC)
PAST = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


async def _seed_two_courses(session: AsyncSession, world) -> SimpleNamespace:  # type: ignore[no-untyped-def]
    await courses_repo.enroll(session, world.course_b.id, world.student.id)
    await session.commit()

    seeded_a = await seed_document(
        session,
        world,
        topic_specs=[
            {"title": "A Released", "chunk_count": 2, "released": True},
            {"title": "A Draft", "chunk_count": 1},
        ],
    )
    seeded_b = await seed_document(
        session,
        world,
        course_id=world.course_b.id,
        uploaded_by=world.teacher_b.id,
        topic_specs=[{"title": "B Released", "chunk_count": 1, "released": True}],
    )

    topic_a = seeded_a.named("A Released").topic
    topic_b = seeded_b.named("B Released").topic
    topic_a.released_at = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    topic_b.released_at = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    await session.commit()

    task_a = await seed_published_task(session, topic_a, due_at=FUTURE)
    task_b = await seed_published_task(session, topic_b, due_at=PAST)
    # A published task on a draft topic must still be invisible to students.
    draft_task = await seed_published_task(session, seeded_a.named("A Draft").topic)

    return SimpleNamespace(
        seeded_a=seeded_a,
        seeded_b=seeded_b,
        topic_a=topic_a,
        topic_b=topic_b,
        task_a=task_a,
        task_b=task_b,
        draft_task=draft_task,
    )


async def test_student_sees_released_tasks_from_both_courses(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    data = await _seed_two_courses(session, world)
    student = await client_factory()
    await login(student, world.student.email)

    response = await student.get("/api/v1/student/tasks")
    assert response.status_code == 200, response.text
    body = response.json()
    titles = [item["title"] for item in body["items"]]

    assert titles == ["B Released", "A Released"]  # ordered by due date
    assert {item["course"]["code"] for item in body["items"]} == {"SCI101", "MATH101"}
    assert "A Draft" not in response.text
    assert body["next_cursor"] is None

    by_id = {item["id"]: item for item in body["items"]}
    assert by_id[str(data.task_b.id)]["overdue"] is True
    assert by_id[str(data.task_a.id)]["overdue"] is False
    assert by_id[str(data.task_a.id)]["status"] == "incomplete"


async def test_status_filters_course_filter_and_sort(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    data = await _seed_two_courses(session, world)
    student = await client_factory()
    await login(student, world.student.email)

    completed = await student.patch(
        f"/api/v1/student/tasks/{data.task_a.id}",
        json={"status": "completed"},
    )
    assert completed.status_code == 200, completed.text

    incomplete = await student.get("/api/v1/student/tasks?status=incomplete")
    assert [item["title"] for item in incomplete.json()["items"]] == ["B Released"]

    done = await student.get("/api/v1/student/tasks?status=completed")
    assert [item["title"] for item in done.json()["items"]] == ["A Released"]

    only_b = await student.get(f"/api/v1/student/tasks?course_id={world.course_b.id}")
    assert [item["title"] for item in only_b.json()["items"]] == ["B Released"]

    by_release = await student.get("/api/v1/student/tasks?sort=released_at")
    assert [item["title"] for item in by_release.json()["items"]] == [
        "B Released",
        "A Released",
    ]


async def test_completion_is_idempotent_and_reversible(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    data = await _seed_two_courses(session, world)
    student = await client_factory()
    await login(student, world.student.email)
    url = f"/api/v1/student/tasks/{data.task_a.id}"

    first = await student.patch(url, json={"status": "completed"})
    assert first.status_code == 200, first.text
    first_completed_at = first.json()["completed_at"]
    assert first_completed_at is not None

    second = await student.patch(url, json={"status": "completed"})
    assert second.status_code == 200, second.text
    assert second.json()["completed_at"] == first_completed_at

    reopened = await student.patch(url, json={"status": "incomplete"})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "incomplete"
    assert reopened.json()["completed_at"] is None

    count = await session.execute(
        select(func.count()).select_from(TaskProgress).where(TaskProgress.task_id == data.task_a.id)
    )
    assert count.scalar_one() == 1


async def test_unrelease_hides_task_but_keeps_completion_history(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    data = await _seed_two_courses(session, world)
    app.dependency_overrides[get_vector_store] = lambda: fake_vector_store(data.seeded_a)

    student = await client_factory()
    await login(student, world.student.email)
    completed = await student.patch(
        f"/api/v1/student/tasks/{data.task_a.id}",
        json={"status": "completed"},
    )
    assert completed.status_code == 200, completed.text

    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    unreleased = await teacher.post(f"/api/v1/topics/{data.topic_a.id}/unrelease")
    assert unreleased.status_code == 200, unreleased.text

    hidden = await student.get("/api/v1/student/tasks")
    assert [item["title"] for item in hidden.json()["items"]] == ["B Released"]
    blocked = await student.patch(
        f"/api/v1/student/tasks/{data.task_a.id}",
        json={"status": "completed"},
    )
    assert blocked.status_code == 404

    progress = await tasks_repo.get_progress(
        session,
        task_id=data.task_a.id,
        student_id=world.student.id,
    )
    assert progress is not None
    assert progress.status is TaskProgressStatus.COMPLETED

    released_again = await teacher.post(
        f"/api/v1/topics/{data.topic_a.id}/release",
        json={"week_no": 1, "due_at": FUTURE.isoformat()},
    )
    assert released_again.status_code == 200, released_again.text

    restored = await student.get("/api/v1/student/tasks")
    restored_by_id = {item["id"]: item for item in restored.json()["items"]}
    assert restored_by_id[str(data.task_a.id)]["status"] == "completed"
    assert restored_by_id[str(data.task_a.id)]["completed_at"] is not None


async def test_cross_course_task_is_not_visible_or_completable(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    other_course = Course(code="HIST101", title="History", created_by=world.admin.id)
    session.add(other_course)
    await session.commit()

    seeded = await seed_document(
        session,
        world,
        course_id=other_course.id,
        topic_specs=[{"title": "Other Course Task", "chunk_count": 1, "released": True}],
    )
    other_task = await seed_published_task(session, seeded.named("Other Course Task").topic)

    student = await client_factory()
    await login(student, world.student.email)

    listed = await student.get("/api/v1/student/tasks")
    assert listed.status_code == 200, listed.text
    assert "Other Course Task" not in listed.text

    patched = await student.patch(
        f"/api/v1/student/tasks/{other_task.id}",
        json={"status": "completed"},
    )
    assert patched.status_code == 404

    filtered = await student.get(f"/api/v1/student/tasks?course_id={other_course.id}")
    assert filtered.status_code == 404


async def test_dashboard_counts_due_soon_and_announcements(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    data = await _seed_two_courses(session, world)
    session.add(
        Announcement(
            course_id=world.course_a.id,
            author_id=world.teacher.id,
            title="Welcome",
            body="Read the syllabus.",
            publish_at=datetime.now(UTC),
        )
    )
    await session.commit()

    student = await client_factory()
    await login(student, world.student.email)
    await student.patch(
        f"/api/v1/student/tasks/{data.task_a.id}",
        json={"status": "completed"},
    )

    response = await student.get("/api/v1/student/dashboard")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["counts"] == {"pending": 1, "completed": 1, "overdue": 1}
    assert [item["title"] for item in body["due_soon"]] == ["B Released"]
    assert [item["title"] for item in body["announcements"]] == ["Welcome"]
    assert "A Draft" not in response.text


async def test_release_fans_out_one_notification_per_enrolled_student(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Fresh Release", "chunk_count": 1}],
    )
    topic = seeded.named("Fresh Release").topic
    app.dependency_overrides[get_vector_store] = lambda: fake_vector_store(seeded)

    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    released = await teacher.post(
        f"/api/v1/topics/{topic.id}/release",
        json={"week_no": 3, "due_at": FUTURE.isoformat()},
    )
    assert released.status_code == 200, released.text
    task_id = released.json()["task_id"]

    student = await client_factory()
    await login(student, world.student.email)
    inbox = await student.get("/api/v1/notifications")
    assert inbox.status_code == 200, inbox.text
    body = inbox.json()
    assert body["unread_count"] == 1
    assert len(body["items"]) == 1
    notification = body["items"][0]
    assert notification["type"] == "task_released"
    assert notification["payload"]["task_id"] == task_id
    assert notification["payload"]["course_code"] == "SCI101"
    assert notification["read"] is False

    unread = await student.get("/api/v1/notifications?unread=true")
    assert unread.json()["unread_count"] == 1

    marked = await student.post(f"/api/v1/notifications/{notification['id']}/read")
    assert marked.status_code == 204, marked.text
    again = await student.post(f"/api/v1/notifications/{notification['id']}/read")
    assert again.status_code == 204, again.text

    after = await student.get("/api/v1/notifications")
    assert after.json()["unread_count"] == 0
    assert after.json()["items"][0]["read"] is True
    assert (await student.get("/api/v1/notifications?unread=true")).json()["items"] == []

    outsider = await client_factory()
    await login(outsider, world.student_b.email)
    outsider_inbox = await outsider.get("/api/v1/notifications")
    assert outsider_inbox.json() == {"items": [], "unread_count": 0}

    teacher_inbox = await teacher.get("/api/v1/notifications")
    assert teacher_inbox.json() == {"items": [], "unread_count": 0}
