"""Shared test fixtures.

Tests run against an in-memory SQLite database so the suite is hermetic and
does not require Docker. Production runs Postgres via Alembic; see
``migrations/`` and ``infra/docker-compose.yml``.
"""

# ruff: noqa: E402

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

TEST_UPLOAD_DIR = Path(tempfile.mkdtemp(prefix="lms-test-uploads-"))

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["SESSION_SECRET"] = "test-secret-not-for-production-0123456789"
os.environ["COOKIE_SECURE"] = "false"
os.environ["INGEST_QUEUE"] = "disabled"
os.environ["REDIS_URL"] = ""
os.environ["UPLOAD_DIR"] = str(TEST_UPLOAD_DIR)

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.analytics_cache import get_analytics_cache
from app.core.assistant_ratelimit import get_assistant_limiter
from app.core.ratelimit import login_limiter
from app.core.roles import CourseRole, Role, UserStatus
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import Course, Enrollment, User
from app.db.session import (
    dispose_engine,
    get_sessionmaker,
    init_engine,
)
from app.main import app
from app.rag.vector_store import get_vector_store
from app.services.permission_service import reset_permission_cache

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
PASSWORD = "Passw0rd!"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _database() -> AsyncIterator[None]:
    engine = init_engine(TEST_DB_URL)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    await dispose_engine()


@pytest_asyncio.fixture(autouse=True)
async def _clean_database(_database: None) -> AsyncIterator[None]:
    async with get_sessionmaker()() as session:
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(delete(table))
        await session.commit()
    await login_limiter.reset()
    await get_assistant_limiter().reset()
    get_analytics_cache().reset()
    reset_permission_cache()
    yield


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as db:
        yield db


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest_asyncio.fixture
async def client_factory() -> AsyncIterator[object]:
    clients: list[AsyncClient] = []

    async def factory() -> AsyncClient:
        http_client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(http_client)
        return http_client

    yield factory
    for http_client in clients:
        await http_client.aclose()


class NoopVectorStore:
    def upsert(self, **kwargs: object) -> None:
        del kwargs

    def query(self, vector: object, **kwargs: object) -> dict[str, object]:
        del vector, kwargs
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def update_metadata(self, **kwargs: object) -> None:
        del kwargs

    def get(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        return {"ids": [], "metadatas": []}

    def delete(self, **kwargs: object) -> None:
        del kwargs

    def count(self, **kwargs: object) -> int:
        del kwargs
        return 0


@pytest.fixture(autouse=True)
def _no_external_vector_store():  # type: ignore[no-untyped-def]
    app.dependency_overrides[get_vector_store] = lambda: NoopVectorStore()
    yield
    app.dependency_overrides.pop(get_vector_store, None)


async def make_user(
    email: str,
    role: Role = Role.STUDENT,
    *,
    name: str | None = None,
    status: UserStatus = UserStatus.ACTIVE,
    password: str = PASSWORD,
) -> User:
    async with get_sessionmaker()() as db:
        user = User(
            name=name or email.split("@")[0],
            email=email.lower(),
            password_hash=hash_password(password),
            role=role,
            status=status,
        )
        db.add(user)
        await db.commit()
        return user


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    csrf = client.cookies.get("lms_csrf")
    assert csrf
    client.headers["X-CSRF-Token"] = csrf


@pytest_asyncio.fixture
async def world() -> SimpleNamespace:
    """Two courses, one teacher each, one student in course A."""
    admin = await make_user("admin@example.com", Role.ADMIN, name="Admin")
    teacher = await make_user("teacher@example.com", Role.TEACHER, name="Teacher A")
    teacher_b = await make_user("teacherb@example.com", Role.TEACHER, name="Teacher B")
    student = await make_user("student@example.com", Role.STUDENT, name="Student A")
    student_b = await make_user("studentb@example.com", Role.STUDENT, name="Student B")

    async with get_sessionmaker()() as db:
        course_a = Course(code="SCI101", title="Science", term="2026-T1", created_by=admin.id)
        course_b = Course(code="MATH101", title="Mathematics", term="2026-T1", created_by=admin.id)
        db.add_all([course_a, course_b])
        await db.flush()
        db.add_all(
            [
                Enrollment(
                    course_id=course_a.id,
                    user_id=teacher.id,
                    course_role=CourseRole.TEACHER,
                ),
                Enrollment(
                    course_id=course_b.id, user_id=teacher_b.id, course_role=CourseRole.TEACHER
                ),
                Enrollment(
                    course_id=course_a.id, user_id=student.id, course_role=CourseRole.STUDENT
                ),
            ]
        )
        await db.commit()
        return SimpleNamespace(
            admin=admin,
            teacher=teacher,
            teacher_b=teacher_b,
            student=student,
            student_b=student_b,
            course_a=course_a,
            course_b=course_b,
        )
