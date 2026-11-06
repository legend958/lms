from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import login


async def test_unauthenticated_requests_are_rejected(client: AsyncClient) -> None:
    response = await client.get("/api/v1/courses")
    assert response.status_code == 401


@pytest.mark.parametrize("email", ["student@example.com", "teacher@example.com"])
async def test_admin_endpoints_reject_non_admins(client_factory, world, email: str) -> None:
    client = await client_factory()
    await login(client, email)

    response = await client.get("/api/v1/admin/users")
    assert response.status_code == 403


async def test_admin_can_list_users(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.admin.email)

    response = await client.get("/api/v1/admin/users")
    assert response.status_code == 200
    emails = {item["email"] for item in response.json()["items"]}
    assert {"admin@example.com", "student@example.com"} <= emails


@pytest.mark.parametrize("email", ["student@example.com", "teacher@example.com"])
async def test_only_admin_can_create_courses(client_factory, world, email: str) -> None:
    client = await client_factory()
    await login(client, email)

    response = await client.post(
        "/api/v1/courses",
        json={"code": f"NEW-{email}", "title": "Nope"},
    )
    assert response.status_code == 403


async def test_admin_can_create_course(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.admin.email)

    response = await client.post(
        "/api/v1/courses",
        json={"code": "PHY101", "title": "Physics", "term": "2026-T1"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["code"] == "PHY101"


async def test_students_cannot_publish_announcements(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.student.email)

    response = await client.post(
        "/api/v1/announcements",
        json={"title": "Hi", "body": "Hello", "course_id": str(world.course_a.id)},
    )
    assert response.status_code == 403


async def test_teacher_can_publish_to_own_course_only(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.teacher.email)

    own = await client.post(
        "/api/v1/announcements",
        json={"title": "Lab", "body": "Bring goggles", "course_id": str(world.course_a.id)},
    )
    assert own.status_code == 201, own.text

    other = await client.post(
        "/api/v1/announcements",
        json={"title": "Nope", "body": "Not mine", "course_id": str(world.course_b.id)},
    )
    assert other.status_code == 404

    global_message = await client.post(
        "/api/v1/announcements",
        json={"title": "Nope", "body": "Institution-wide"},
    )
    assert global_message.status_code == 403


async def test_students_cannot_read_enrollments_of_other_courses(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.student.email)

    response = await client.get(f"/api/v1/courses/{world.course_b.id}/enrollments")
    assert response.status_code == 404


async def test_role_matrix_smoke(client_factory, world) -> None:
    """One representative read and write per role."""
    expectations: dict[str, tuple[int, int]] = {
        "student@example.com": (200, 403),
        "teacher@example.com": (200, 403),
        "admin@example.com": (200, 201),
    }
    for email, (read_status, write_status) in expectations.items():
        client = await client_factory()
        await login(client, email)
        read = await client.get("/api/v1/courses")
        assert read.status_code == read_status, (email, read.text)
        write = await client.post(
            "/api/v1/courses",
            json={"code": f"R-{email}", "title": "Role matrix"},
        )
        assert write.status_code == write_status, (email, write.text)
