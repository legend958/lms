from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from tests.conftest import login


async def _publish(
    client: AsyncClient,
    *,
    title: str,
    body: str = "Body",
    course_id: str | None = None,
    publish_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict:
    payload: dict[str, str | None] = {"title": title, "body": body, "course_id": course_id}
    if publish_at is not None:
        payload["publish_at"] = publish_at.isoformat()
    if expires_at is not None:
        payload["expires_at"] = expires_at.isoformat()
    response = await client.post("/api/v1/announcements", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_student_feed_respects_course_scope_and_windows(client_factory, world) -> None:
    now = datetime.now(UTC)
    admin = await client_factory()
    await login(admin, world.admin.email)
    await _publish(admin, title="Institution notice")

    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    await _publish(teacher, title="Science published", course_id=str(world.course_a.id))
    await _publish(
        teacher,
        title="Science future",
        course_id=str(world.course_a.id),
        publish_at=now + timedelta(days=2),
    )
    await _publish(
        teacher,
        title="Science expired",
        course_id=str(world.course_a.id),
        publish_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
    )

    teacher_b = await client_factory()
    await login(teacher_b, world.teacher_b.email)
    await _publish(teacher_b, title="Maths only", course_id=str(world.course_b.id))

    student = await client_factory()
    await login(student, world.student.email)
    response = await student.get("/api/v1/announcements")
    assert response.status_code == 200
    titles = {item["title"] for item in response.json()}
    assert titles == {"Institution notice", "Science published"}


async def test_student_cannot_request_scheduled_feed(client_factory, world) -> None:
    student = await client_factory()
    await login(student, world.student.email)
    response = await student.get("/api/v1/announcements?scheduled=true")
    assert response.status_code == 403


async def test_teacher_scheduled_feed_includes_future(client_factory, world) -> None:
    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    await _publish(
        teacher,
        title="Tomorrow",
        course_id=str(world.course_a.id),
        publish_at=datetime.now(UTC) + timedelta(days=1),
    )

    response = await teacher.get("/api/v1/announcements?scheduled=true")
    assert response.status_code == 200
    assert "Tomorrow" in {item["title"] for item in response.json()}


async def test_mark_read_is_idempotent(client_factory, world) -> None:
    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    announcement = await _publish(teacher, title="Read me", course_id=str(world.course_a.id))

    student = await client_factory()
    await login(student, world.student.email)
    first = await student.post(f"/api/v1/announcements/{announcement['id']}/read")
    second = await student.post(f"/api/v1/announcements/{announcement['id']}/read")
    assert first.status_code == 204
    assert second.status_code == 204

    feed = await student.get("/api/v1/announcements")
    matching = [item for item in feed.json() if item["id"] == announcement["id"]]
    assert matching and matching[0]["read"] is True


async def test_teacher_cannot_edit_another_teachers_announcement(client_factory, world) -> None:
    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    announcement = await _publish(teacher, title="Mine", course_id=str(world.course_a.id))

    teacher_b = await client_factory()
    await login(teacher_b, world.teacher_b.email)
    response = await teacher_b.patch(
        f"/api/v1/announcements/{announcement['id']}", json={"title": "Theirs"}
    )
    assert response.status_code == 403


async def test_admin_can_edit_any_announcement(client_factory, world) -> None:
    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    announcement = await _publish(teacher, title="Original", course_id=str(world.course_a.id))

    admin = await client_factory()
    await login(admin, world.admin.email)
    response = await admin.patch(
        f"/api/v1/announcements/{announcement['id']}", json={"title": "Edited"}
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Edited"
