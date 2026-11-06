from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select

from app.core.roles import UserStatus
from app.db.models import AuditLog
from app.db.session import get_sessionmaker
from tests.conftest import PASSWORD, login, make_user


async def test_login_me_and_logout(client: AsyncClient, world) -> None:
    await login(client, world.admin.email)

    assert client.cookies.get("lms_access")
    assert client.cookies.get("lms_refresh")
    assert client.cookies.get("lms_role") == "admin"

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "admin@example.com"
    assert body["role"] == "admin"
    assert body["courses"] == []

    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    assert client.cookies.get("lms_access") in (None, "")

    after = await client.get("/api/v1/auth/me")
    assert after.status_code == 401


async def test_wrong_password_is_audited(client: AsyncClient, world) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": world.student.email, "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["status"] == 401

    async with get_sessionmaker()() as session:
        rows = list((await session.execute(select(AuditLog))).scalars())
    assert any(row.action == "auth.login_failed" for row in rows)


async def test_inactive_user_cannot_log_in(client: AsyncClient) -> None:
    await make_user("inactive@example.com", status=UserStatus.INACTIVE)
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "inactive@example.com", "password": PASSWORD},
    )
    assert response.status_code == 401


async def test_refresh_rotates_and_revokes_old_token(client: AsyncClient, world) -> None:
    await login(client, world.student.email)
    old_refresh = client.cookies.get("lms_refresh")
    assert old_refresh

    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    new_refresh = client.cookies.get("lms_refresh")
    assert new_refresh and new_refresh != old_refresh

    client.cookies.set("lms_refresh", old_refresh)
    replay = await client.post("/api/v1/auth/refresh")
    assert replay.status_code == 401


async def test_unsafe_request_requires_csrf(client: AsyncClient, world) -> None:
    await login(client, world.student.email)
    del client.headers["X-CSRF-Token"]

    response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 403
    assert response.json()["type"].endswith("/csrf")


async def test_login_is_rate_limited(client: AsyncClient, world) -> None:
    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": world.student.email, "password": "wrong"},
        )
        assert response.status_code == 401

    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": world.student.email, "password": "wrong"},
    )
    assert blocked.status_code == 429
    assert "retry-after" in blocked.headers
