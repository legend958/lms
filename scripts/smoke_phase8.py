"""Live Phase 8 smoke checks against a running Compose stack.

Run inside the api container so the script can use the same database, Chroma
collection, and upload volume as the service:

    docker compose --env-file .env -f infra/docker-compose.yml cp \
        scripts/smoke_phase8.py api:/tmp/smoke_phase8.py
    docker compose --env-file .env -f infra/docker-compose.yml exec -T api \
        python /tmp/smoke_phase8.py

The script covers the manual Phase 8 walkthrough: a five-row CSV import with
two malformed rows, the assistant permission toggle returning 403 to a student,
and a hard document delete that removes Postgres rows, Chroma vectors, and the
stored PDF while retaining an audit snapshot.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import uuid
from typing import Any

import httpx
from sqlalchemy import func, select

from app.core.ids import uuid7
from app.core.roles import (
    CourseStatus,
    DocumentStatus,
    TopicReleaseState,
    TopicSource,
)
from app.db.models import Chunk, Course, Document, Topic, User
from app.db.session import get_sessionmaker
from app.rag.vector_store import get_vector_store
from app.services.storage import document_key, get_storage

BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("SMOKE_ADMIN_EMAIL", "admin@lms.example.com")
ADMIN_PASSWORD = os.environ.get("SMOKE_ADMIN_PASSWORD", "ChangeMe123!")
STUDENT_PASSWORD = "Phase8Smoke!23"
PDF_BYTES = b"%PDF-1.7\n% phase 8 smoke\n"

CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    if not condition:
        raise AssertionError(message)
    CHECKS += 1
    print(f"  ok  {message}")


class Api:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=BASE_URL, timeout=90, follow_redirects=True)
        self.csrf: str | None = None

    def bootstrap(self) -> None:
        response = self.client.get("/api/v1/auth/csrf")
        response.raise_for_status()
        self.csrf = response.json()["csrf_token"]

    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf} if self.csrf else {}

    def login(self, email: str, password: str) -> dict[str, Any]:
        self.bootstrap()
        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers=self.headers(),
        )
        if response.status_code != 200:
            raise SystemExit(f"login failed for {email}: {response.status_code} {response.text}")
        self.csrf = self.client.cookies.get("lms_csrf") or self.csrf
        return response.json()["user"]

    def get(self, path: str) -> httpx.Response:
        return self.client.get(path)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.client.post(path, headers=self.headers(), **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.client.patch(path, headers=self.headers(), **kwargs)

    def delete(self, path: str) -> httpx.Response:
        return self.client.delete(path, headers=self.headers())


async def find_live_course() -> tuple[uuid.UUID, uuid.UUID, str]:
    async with get_sessionmaker()() as session:
        admin = await session.scalar(select(User).where(User.email == ADMIN_EMAIL))
        if admin is None:
            raise SystemExit(f"admin {ADMIN_EMAIL} not found")
        course = await session.scalar(
            select(Course)
            .where(Course.status == CourseStatus.ACTIVE)
            .order_by(Course.code)
            .limit(1)
        )
        if course is None:
            raise SystemExit("no active course to attach the smoke document to")
        return admin.id, course.id, course.code


async def seed_document(course_id: uuid.UUID, uploader_id: uuid.UUID, suffix: str) -> uuid.UUID:
    document_id = uuid7()
    async with get_sessionmaker()() as session:
        document = Document(
            id=document_id,
            course_id=course_id,
            uploaded_by=uploader_id,
            filename=f"phase8-smoke-{suffix}.pdf",
            checksum=hashlib.sha256(f"phase8-{suffix}".encode()).hexdigest(),
            size_bytes=len(PDF_BYTES),
            page_count=1,
            status=DocumentStatus.READY,
            pipeline_version=1,
            topic_count=1,
            topics_source=TopicSource.LLM,
        )
        session.add(document)
        await session.flush()
        topic = Topic(
            document_id=document.id,
            course_id=course_id,
            title=f"Smoke topic {suffix}",
            summary="Seeded by the Phase 8 smoke check.",
            page_start=1,
            page_end=1,
            order_index=0,
            release_state=TopicReleaseState.DRAFT,
            needs_review=False,
        )
        session.add(topic)
        await session.flush()
        session.add(
            Chunk(
                vector_id=f"{document.id}:0",
                document_id=document.id,
                course_id=course_id,
                topic_id=topic.id,
                chunk_index=0,
                text="The smoke check verifies that deleting a document removes its vectors.",
                page_start=1,
                page_end=1,
                section_title="Smoke",
                token_count=14,
                released=False,
            )
        )
        await session.commit()
    key = document_key(course_id, document_id)
    get_storage().put(key, PDF_BYTES)
    get_vector_store().upsert(
        ids=[f"{document_id}:0"],
        embeddings=[[0.01] * 384],
        documents=["phase8 smoke vector"],
        metadatas=[
            {
                "course_id": str(course_id),
                "document_id": str(document_id),
                "topic_id": "unassigned",
                "page_start": 1,
                "page_end": 1,
                "section_title": "Smoke",
                "released": False,
                "pipeline_version": 1,
                "chunk_index": 0,
            }
        ],
    )
    return document_id


async def verify_document_rows(document_id: uuid.UUID) -> tuple[bool, bool, bool]:
    async with get_sessionmaker()() as session:
        document = await session.get(Document, document_id)
        topics = await session.scalar(
            select(func.count(Topic.id)).where(Topic.document_id == document_id)
        )
        chunks = await session.scalar(
            select(func.count(Chunk.id)).where(Chunk.document_id == document_id)
        )
        return document is None, int(topics or 0) == 0, int(chunks or 0) == 0


def delete_imported_users(admin: Api, user_ids: list[str]) -> None:
    for user_id in user_ids:
        response = admin.delete(f"/api/v1/admin/users/{user_id}")
        check(response.status_code == 204, f"cleanup deleted imported user {user_id[:8]}")


async def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    admin_id, course_id, course_code = await find_live_course()

    print("1. Admin login and seeded capability matrix")
    admin = Api()
    admin_user = admin.login(ADMIN_EMAIL, ADMIN_PASSWORD)
    check(admin_user["role"] == "admin", "logged in as admin")
    permissions = admin.get("/api/v1/admin/permissions")
    check(permissions.status_code == 200, "permission matrix readable")
    rows = {row["key"]: row for row in permissions.json()}
    check(
        set(rows)
        == {
            "student.self_enroll",
            "teacher.create_course",
            "student.assistant_enabled",
            "quiz.auto_approve",
        },
        "four capability keys are seeded",
    )
    check(rows["student.assistant_enabled"]["enabled"] is True, "assistant defaults to on")

    print("2. CSV import: 5 rows, 3 valid, 2 malformed")
    csv_body = (
        "name,email,role,course_codes\n"
        f"Ada {suffix},ada.{suffix}@example.com,student,{course_code}\n"
        f"Grace {suffix},grace.{suffix}@example.com,teacher,{course_code}\n"
        f"Alan {suffix},alan.{suffix}@example.com,student,\n"
        f"Broken {suffix},not-an-email,student,\n"
        f"Wizard {suffix},wizard.{suffix}@example.com,wizard,\n"
    )
    imported = admin.post(
        "/api/v1/admin/users/import",
        files={"file": ("phase8-smoke.csv", csv_body.encode(), "text/csv")},
        data={"mode": "partial"},
    )
    check(imported.status_code == 200, "CSV import returned 200")
    report = imported.json()
    check(report["total"] == 5, "report covers all five rows")
    check(report["created"] == 3, "three valid rows created")
    check(report["failed"] == 2, "two malformed rows failed")
    created_rows = [row for row in report["rows"] if row["status"] == "created"]
    check(all(row["temporary_password"] for row in created_rows), "temporary passwords returned")
    bad_rows = [row for row in report["rows"] if row["status"] == "error"]
    check(
        {row["row_number"] for row in bad_rows} == {5, 6},
        "malformed email and role are reported by row number",
    )

    print("3. Assistant capability gates a real student request")
    temporary = admin.post(
        "/api/v1/admin/users",
        json={
            "name": f"Phase 8 Student {suffix}",
            "email": f"phase8.student.{suffix}@example.com",
            "role": "student",
            "password": STUDENT_PASSWORD,
        },
    )
    check(temporary.status_code == 201, "temporary student created")
    student_id = temporary.json()["id"]
    student = Api()
    student.login(f"phase8.student.{suffix}@example.com", STUDENT_PASSWORD)

    disabled = admin.patch(
        "/api/v1/admin/permissions",
        json={
            "key": "student.assistant_enabled",
            "role": "student",
            "enabled": False,
        },
    )
    check(disabled.status_code == 200, "assistant capability disabled")
    asked = student.post("/api/v1/student/ask", json={"question": "What is a covalent bond?"})
    check(asked.status_code == 403, "student assistant request returns 403 while disabled")
    restored = admin.patch(
        "/api/v1/admin/permissions",
        json={
            "key": "student.assistant_enabled",
            "role": "student",
            "enabled": True,
        },
    )
    check(restored.status_code == 200, "assistant capability restored")

    print("4. Document delete removes rows, vectors, and storage")
    document_id = await seed_document(course_id, admin_id, suffix)
    key = document_key(course_id, document_id)
    store = get_vector_store()
    check(get_storage().exists(key), "seeded PDF exists in storage")
    check(
        bool(store.get(where={"document_id": str(document_id)}).get("ids")), "seeded vector exists"
    )

    content = admin.get(f"/api/v1/admin/content?course_id={course_id}")
    check(content.status_code == 200, "content repository readable")
    row = next(
        (item for item in content.json() if item["id"] == str(document_id)),
        None,
    )
    check(
        row is not None and row["released_topics"] == 0, "seeded document is listed and unreleased"
    )
    check(row is not None and row["chunk_count"] == 1, "content row aggregates chunks")

    deleted = admin.delete(f"/api/v1/admin/content/{document_id}")
    check(deleted.status_code == 204, "document deleted through the admin endpoint")
    document_gone, topics_gone, chunks_gone = await verify_document_rows(document_id)
    check(document_gone, "document row removed from Postgres")
    check(topics_gone, "topic rows removed from Postgres")
    check(chunks_gone, "chunk rows removed from Postgres")
    check(not get_storage().exists(key), "stored PDF removed")
    check(
        not bool(store.get(where={"document_id": str(document_id)}).get("ids")),
        "Chroma vectors removed",
    )
    listed = admin.get(f"/api/v1/admin/content?course_id={course_id}")
    check(
        all(item["id"] != str(document_id) for item in listed.json()),
        "deleted document no longer listed",
    )

    audit = admin.get(f"/api/v1/admin/audit-logs?action=document.delete&entity_id={document_id}")
    check(audit.status_code == 200, "audit log readable")
    entries = audit.json()["items"]
    check(len(entries) == 1, "delete wrote one audit row")
    check(entries[0]["before"]["filename"].startswith("phase8-smoke-"), "audit keeps the snapshot")
    check(bool(entries[0]["request_id"]), "audit row has a request id")

    print("5. System health")
    health = admin.get("/api/v1/admin/system/health")
    check(health.status_code == 200, "health endpoint returned 200")
    body = health.json()
    check(body["checks"]["postgres"] == "ok", "Postgres is ok")
    check(body["checks"]["chroma"] == "ok", "Chroma is ok")
    check(body["checks"]["groq"] in {"ok", "degraded", "not_configured"}, "Groq is reported")
    check(body["status"] in {"ok", "degraded"}, "health status is ok or degraded")

    print("6. Cleanup")
    delete_imported_users(admin, [row["user_id"] for row in created_rows])
    check(
        admin.delete(f"/api/v1/admin/users/{student_id}").status_code == 204,
        "temporary student removed",
    )

    print(f"\nPhase 8 live smoke passed: {CHECKS} checks")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except httpx.HTTPError as exc:
        print(f"\nHTTP FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
