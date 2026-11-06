"""CSV user import: per-row reporting, partial vs atomic, and audit."""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import CourseRole
from app.db.models import AuditLog, Enrollment, User
from tests.conftest import login

HEADER = "name,email,role,course_codes\n"


async def _import(
    client: AsyncClient,
    body: str,
    *,
    mode: str = "partial",
    filename: str = "users.csv",
):
    return await client.post(
        "/api/v1/admin/users/import",
        files={"file": (filename, body.encode("utf-8"), "text/csv")},
        data={"mode": mode},
    )


async def test_partial_import_reports_every_row(
    client_factory, world, session: AsyncSession
) -> None:
    body = HEADER + (
        "Alice,alice@example.com,student,SCI101\n"
        "Bob,bob@example.com,teacher,SCI101\n"
        "Carol,carol@example.com,student,\n"
        "Broken,bad-email,student,\n"
        "Dave,dave@example.com,wizard,\n"
        "Admin,admin@example.com,admin,\n"
        "Alice Again,alice@example.com,student,\n"
    )
    client = await client_factory()
    await login(client, world.admin.email)

    response = await _import(client, body)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["mode"] == "partial"
    assert report["total"] == 7
    assert report["created"] == 3
    assert report["failed"] == 2
    assert report["skipped"] == 2

    by_row = {row["row_number"]: row for row in report["rows"]}
    assert by_row[2]["status"] == "created"
    assert by_row[2]["temporary_password"]
    assert by_row[5]["status"] == "error"
    assert by_row[5]["reason"] == "Invalid email address"
    assert by_row[6]["status"] == "error"
    assert "Role must be" in by_row[6]["reason"]
    assert by_row[7]["status"] == "skipped"
    assert "already exists" in by_row[7]["reason"]
    assert by_row[8]["status"] == "skipped"
    assert "Duplicate email" in by_row[8]["reason"]

    alice = await session.scalar(select(User).where(User.email == "alice@example.com"))
    assert alice is not None
    enrollment = await session.scalar(
        select(Enrollment).where(
            Enrollment.user_id == alice.id,
            Enrollment.course_id == world.course_a.id,
        )
    )
    assert enrollment is not None
    assert enrollment.course_role is CourseRole.STUDENT

    bob = await session.scalar(select(User).where(User.email == "bob@example.com"))
    assert bob is not None
    bob_enrollment = await session.scalar(
        select(Enrollment).where(
            Enrollment.user_id == bob.id,
            Enrollment.course_id == world.course_a.id,
        )
    )
    assert bob_enrollment is not None
    assert bob_enrollment.course_role is CourseRole.TEACHER

    audit = await session.scalar(select(AuditLog).where(AuditLog.action == "user.import"))
    assert audit is not None
    assert audit.after is not None
    assert audit.after["created"] == 3
    assert audit.after["skipped"] == 2
    assert audit.after["failed"] == 2
    assert len(audit.after["user_ids"]) == 3
    assert audit.request_id


async def test_imported_user_can_log_in_with_temporary_password(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.admin.email)
    report = (await _import(client, HEADER + "Eve,eve@example.com,student,\n")).json()
    password = report["rows"][0]["temporary_password"]
    assert password

    fresh = await client_factory()
    await login(fresh, "eve@example.com", password)
    me = await fresh.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "eve@example.com"


async def test_atomic_import_writes_nothing_when_a_row_fails(
    client_factory, world, session: AsyncSession
) -> None:
    body = HEADER + (
        "Alice,alice@example.com,student,\n"
        "Broken,bad-email,student,\n"
        "Bob,bob@example.com,student,\n"
    )
    client = await client_factory()
    await login(client, world.admin.email)

    response = await _import(client, body, mode="atomic")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["mode"] == "atomic"
    assert report["total"] == 3
    assert report["created"] == 0
    assert report["failed"] == 1
    assert report["skipped"] == 2
    assert {row["status"] for row in report["rows"]} == {"skipped", "error"}

    count = await session.scalar(
        select(func.count(User.id)).where(User.email.in_(["alice@example.com", "bob@example.com"]))
    )
    assert count == 0

    audit = await session.scalar(select(AuditLog).where(AuditLog.action == "user.import"))
    assert audit is not None
    assert audit.after is not None
    assert audit.after["created"] == 0
    assert audit.after["user_ids"] == []


async def test_atomic_import_commits_all_valid_rows(
    client_factory, world, session: AsyncSession
) -> None:
    body = HEADER + ("Alice,alice@example.com,student,SCI101\nBob,bob@example.com,teacher,SCI101\n")
    client = await client_factory()
    await login(client, world.admin.email)

    response = await _import(client, body, mode="atomic")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["created"] == 2
    assert report["failed"] == 0
    assert all(row["status"] == "created" for row in report["rows"])

    count = await session.scalar(
        select(func.count(User.id)).where(User.email.in_(["alice@example.com", "bob@example.com"]))
    )
    assert count == 2


async def test_header_and_encoding_validation(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.admin.email)

    missing_role = await _import(client, "name,email\nAlice,alice@example.com\n")
    assert missing_role.status_code == 422, missing_role.text
    assert "role" in missing_role.json()["detail"]

    empty = await client.post(
        "/api/v1/admin/users/import",
        files={"file": ("users.csv", b"", "text/csv")},
        data={"mode": "partial"},
    )
    assert empty.status_code == 422

    bad_encoding = await client.post(
        "/api/v1/admin/users/import",
        files={"file": ("users.csv", b"\xff\xfe\x00bad", "text/csv")},
        data={"mode": "partial"},
    )
    assert bad_encoding.status_code == 422

    bad_mode = await _import(client, HEADER + "Alice,alice@example.com,student,\n", mode="eventual")
    assert bad_mode.status_code == 422


async def test_missing_fields_and_unknown_course_are_row_errors(
    client_factory, world, session: AsyncSession
) -> None:
    body = HEADER + (
        ",missing.name@example.com,student,\nNora,nora@example.com,student,UNKNOWN101\n"
    )
    client = await client_factory()
    await login(client, world.admin.email)

    response = await _import(client, body)
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    assert rows[0]["status"] == "error"
    assert "Name must be" in rows[0]["reason"]
    assert rows[1]["status"] == "error"
    assert "UNKNOWN101" in rows[1]["reason"]

    nora = await session.scalar(select(User).where(User.email == "nora@example.com"))
    assert nora is None


async def test_import_uses_uuid_user_ids(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.admin.email)
    report = (await _import(client, HEADER + "Zoe,zoe@example.com,student,\n")).json()
    user_id = report["rows"][0]["user_id"]
    assert uuid.UUID(user_id)
