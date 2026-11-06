"""Permission matrix behavior, admin bypass, and capability enforcement."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import Role
from app.services.permission_service import (
    ASSISTANT_ENABLED,
    QUIZ_AUTO_APPROVE,
    SELF_ENROLL,
    TEACHER_CREATE_COURSE,
    get_permission_service,
)
from tests.assistant_utils import FakeAnswerGenerator, StubVectorStore, assistant_overrides
from tests.conftest import login
from tests.fakes import FakeEmbedder


async def _toggle(
    client: AsyncClient,
    *,
    key: str,
    role: str,
    enabled: bool,
) -> None:
    response = await client.patch(
        "/api/v1/admin/permissions",
        json={"key": key, "role": role, "enabled": enabled},
    )
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is enabled


async def test_permissions_are_admin_only(client_factory, world) -> None:
    for email in (world.student.email, world.teacher.email):
        client = await client_factory()
        await login(client, email)

        assert (await client.get("/api/v1/admin/permissions")).status_code == 403
        assert (
            await client.patch(
                "/api/v1/admin/permissions",
                json={"key": ASSISTANT_ENABLED, "role": "student", "enabled": False},
            )
        ).status_code == 403
        assert (await client.get("/api/v1/admin/audit-logs")).status_code == 403
        assert (await client.get("/api/v1/admin/content")).status_code == 403
        assert (await client.get("/api/v1/admin/system/health")).status_code == 403
        imported = await client.post(
            "/api/v1/admin/users/import",
            files={"file": ("users.csv", b"name,email,role\nA,a@example.com,student\n")},
            data={"mode": "partial"},
        )
        assert imported.status_code == 403


async def test_matrix_lists_seeded_capabilities(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.admin.email)

    response = await client.get("/api/v1/admin/permissions")
    assert response.status_code == 200, response.text
    rows = {row["key"]: row for row in response.json()}
    assert set(rows) == {
        SELF_ENROLL,
        TEACHER_CREATE_COURSE,
        ASSISTANT_ENABLED,
        QUIZ_AUTO_APPROVE,
    }
    assert rows[ASSISTANT_ENABLED]["enabled"] is True
    assert rows[SELF_ENROLL]["enabled"] is False
    assert rows[SELF_ENROLL]["affected_users"] == 2


async def test_permission_update_is_audited(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.admin.email)

    await _toggle(client, key=ASSISTANT_ENABLED, role="student", enabled=False)

    audit = await client.get("/api/v1/admin/audit-logs?action=permission.update")
    assert audit.status_code == 200, audit.text
    item = audit.json()["items"][0]
    assert item["entity_type"] == "permission"
    assert item["entity_id"] == f"{ASSISTANT_ENABLED}:student"
    assert item["before"]["enabled"] is True
    assert item["after"]["enabled"] is False
    assert item["actor_name"] == "Admin"
    assert item["request_id"]


async def test_assistant_toggle_changes_student_behavior(client_factory, world) -> None:
    admin = await client_factory()
    await login(admin, world.admin.email)
    student = await client_factory()
    await login(student, world.student.email)

    with assistant_overrides(
        embedder=FakeEmbedder(),
        vector_store=StubVectorStore(),
        generator=FakeAnswerGenerator(),
    ):
        enabled = await student.post(
            "/api/v1/student/ask",
            json={"question": "What is a covalent bond?"},
        )
        assert enabled.status_code == 200, enabled.text

        await _toggle(admin, key=ASSISTANT_ENABLED, role="student", enabled=False)

        disabled = await student.post(
            "/api/v1/student/ask",
            json={"question": "What is a covalent bond?"},
        )
        assert disabled.status_code == 403, disabled.text
        assert "disabled" in disabled.json()["detail"].lower()


async def test_admin_bypasses_disabled_capabilities(session: AsyncSession, world) -> None:
    service = get_permission_service()
    assert await service.can(session, Role.ADMIN, ASSISTANT_ENABLED) is True
    assert await service.can(session, Role.ADMIN, SELF_ENROLL) is True


async def test_teacher_course_creation_requires_capability(client_factory, world) -> None:
    admin = await client_factory()
    await login(admin, world.admin.email)
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    denied = await teacher.post(
        "/api/v1/courses",
        json={"code": "BIO101", "title": "Biology"},
    )
    assert denied.status_code == 403, denied.text

    await _toggle(admin, key=TEACHER_CREATE_COURSE, role="teacher", enabled=True)

    created = await teacher.post(
        "/api/v1/courses",
        json={"code": "BIO101", "title": "Biology"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["code"] == "BIO101"

    courses = await teacher.get("/api/v1/courses")
    assert "BIO101" in {course["code"] for course in courses.json()}


async def test_self_enroll_is_gated_and_idempotent(client_factory, world) -> None:
    admin = await client_factory()
    await login(admin, world.admin.email)
    student = await client_factory()
    await login(student, world.student.email)

    denied = await student.post(f"/api/v1/courses/{world.course_b.id}/self-enroll")
    assert denied.status_code == 403, denied.text

    await _toggle(admin, key=SELF_ENROLL, role="student", enabled=True)

    first = await student.post(f"/api/v1/courses/{world.course_b.id}/self-enroll")
    assert first.status_code == 200, first.text
    assert first.json()["added"] == 1

    second = await student.post(f"/api/v1/courses/{world.course_b.id}/self-enroll")
    assert second.status_code == 200, second.text
    assert second.json()["added"] == 0

    courses = await student.get("/api/v1/courses")
    assert "MATH101" in {course["code"] for course in courses.json()}


@pytest.mark.parametrize(
    ("key", "role"),
    [
        ("unknown.capability", "student"),
        (ASSISTANT_ENABLED, "teacher"),
    ],
)
async def test_invalid_permission_payloads_are_rejected(
    client_factory, world, key: str, role: str
) -> None:
    client = await client_factory()
    await login(client, world.admin.email)

    response = await client.patch(
        "/api/v1/admin/permissions",
        json={"key": key, "role": role, "enabled": True},
    )
    assert response.status_code == 422, response.text
