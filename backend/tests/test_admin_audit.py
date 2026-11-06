"""Admin audit coverage, content repository, and health reporting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import DocumentStatus
from app.db.models import AuditLog, Chunk, Document, Topic
from app.main import app
from app.rag.vector_store import get_vector_store
from app.services.storage import document_key, get_storage
from tests.conftest import login
from tests.topic_utils import fake_vector_store, seed_document

PDF_BYTES = b"%PDF-1.7\n% fake test pdf\n"


async def _audit_rows(session: AsyncSession, *, action: str) -> list[AuditLog]:
    return list(
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.action == action)
                .order_by(AuditLog.created_at.desc())
            )
        ).scalars()
    )


async def test_content_repository_aggregates_topic_and_chunk_counts(
    client_factory, world, session: AsyncSession
) -> None:
    seeded = await seed_document(
        session,
        world,
        topic_specs=[
            {"title": "Atoms", "chunk_count": 2, "released": True},
            {"title": "Bonds", "chunk_count": 3, "released": False},
        ],
    )
    client = await client_factory()
    await login(client, world.admin.email)

    response = await client.get(f"/api/v1/admin/content?course_id={world.course_a.id}")
    assert response.status_code == 200, response.text
    row = next(item for item in response.json() if item["id"] == str(seeded.document.id))
    assert row["course_code"] == "SCI101"
    assert row["uploader_name"] == "Teacher A"
    assert row["topic_count"] == 2
    assert row["released_topics"] == 1
    assert row["chunk_count"] == 5
    assert row["status"] == "ready"


async def test_admin_delete_removes_rows_vectors_and_storage(
    client_factory, world, session: AsyncSession
) -> None:
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Atoms", "chunk_count": 2, "released": False}],
    )
    document_id = seeded.document.id
    storage = get_storage()
    key = document_key(seeded.document.course_id, document_id)
    storage.put(key, PDF_BYTES)

    fake_store = fake_vector_store(seeded)
    app.dependency_overrides[get_vector_store] = lambda: fake_store
    try:
        client = await client_factory()
        await login(client, world.admin.email)
        response = await client.delete(f"/api/v1/admin/content/{document_id}")
        assert response.status_code == 204, response.text
    finally:
        app.dependency_overrides.pop(get_vector_store, None)

    assert fake_store.count() == 0
    assert storage.exists(key) is False

    assert (
        await session.execute(select(Document).where(Document.id == document_id))
    ).first() is None
    assert (
        await session.execute(select(func.count(Topic.id)).where(Topic.document_id == document_id))
    ).scalar_one() == 0
    assert (
        await session.execute(select(func.count(Chunk.id)).where(Chunk.document_id == document_id))
    ).scalar_one() == 0

    audit = await _audit_rows(session, action="document.delete")
    assert len(audit) == 1
    assert audit[0].entity_id == str(document_id)
    assert audit[0].before is not None
    assert audit[0].before["filename"] == seeded.document.filename
    assert audit[0].after == {"hard_deleted": True, "released_topics": 0}
    assert audit[0].request_id


async def test_delete_is_blocked_while_topics_are_released(
    client_factory, world, session: AsyncSession
) -> None:
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Released topic", "chunk_count": 1, "released": True}],
    )
    client = await client_factory()
    await login(client, world.admin.email)

    response = await client.delete(f"/api/v1/admin/content/{seeded.document.id}")
    assert response.status_code == 409, response.text
    assert "Unrelease" in response.json()["detail"]
    document = await session.get(Document, seeded.document.id)
    assert document is not None


async def test_reingest_and_download_are_audited(
    client_factory, world, session: AsyncSession
) -> None:
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Atoms", "chunk_count": 1}],
    )
    storage = get_storage()
    storage.put(
        document_key(seeded.document.course_id, seeded.document.id),
        PDF_BYTES,
    )
    client = await client_factory()
    await login(client, world.admin.email)

    reingest = await client.post(f"/api/v1/admin/content/{seeded.document.id}/reingest")
    assert reingest.status_code == 202, reingest.text
    assert reingest.json()["status"] == "queued"

    download = await client.get(f"/api/v1/admin/content/{seeded.document.id}/download")
    assert download.status_code == 200, download.text
    payload = download.json()
    assert payload["document_id"] == str(seeded.document.id)
    assert payload["url"].startswith("file:")
    assert payload["expires_in"] == 300

    reingest_audit = await _audit_rows(session, action="document.reingest")
    download_audit = await _audit_rows(session, action="document.download")
    assert len(reingest_audit) == 1
    assert len(download_audit) == 1
    assert download_audit[0].after is not None
    assert download_audit[0].after["filename"] == seeded.document.filename


async def test_audit_viewer_filters_and_paginates(
    client_factory, world, session: AsyncSession
) -> None:
    base = datetime.now(UTC) - timedelta(minutes=10)
    for index in range(3):
        session.add(
            AuditLog(
                actor_id=world.admin.id,
                action="test.action",
                entity_type="test",
                entity_id=str(index),
                before={"index": index},
                after={"index": index + 1},
                ip="127.0.0.1",
                request_id=f"req-{index}",
                created_at=base + timedelta(seconds=index),
            )
        )
    await session.commit()

    client = await client_factory()
    await login(client, world.admin.email)

    first = await client.get("/api/v1/admin/audit-logs?action=test.action&limit=2")
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["items"]) == 2
    assert body["items"][0]["actor_name"] == "Admin"
    assert body["items"][0]["before"] == {"index": 2}
    assert body["next_cursor"]

    second = await client.get(
        f"/api/v1/admin/audit-logs?action=test.action&limit=2&cursor={body['next_cursor']}"
    )
    assert second.status_code == 200, second.text
    remaining = second.json()
    assert len(remaining["items"]) == 1
    assert remaining["items"][0]["entity_id"] == "0"
    assert remaining["next_cursor"] is None

    entity = await client.get("/api/v1/admin/audit-logs?entity_type=test&entity_id=1")
    assert entity.status_code == 200
    assert [item["entity_id"] for item in entity.json()["items"]] == ["1"]


async def test_user_lifecycle_mutations_appear_in_audit(
    client_factory, world, session: AsyncSession
) -> None:
    client = await client_factory()
    await login(client, world.admin.email)

    created = await client.post(
        "/api/v1/admin/users",
        json={
            "name": "Audit Target",
            "email": "audit.target@example.com",
            "role": "student",
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]

    updated = await client.patch(
        f"/api/v1/admin/users/{user_id}",
        json={"name": "Audit Target Renamed"},
    )
    assert updated.status_code == 200, updated.text

    deleted = await client.delete(f"/api/v1/admin/users/{user_id}")
    assert deleted.status_code == 204, deleted.text

    actions = {
        row.action
        for row in (
            await session.execute(select(AuditLog).where(AuditLog.entity_id == user_id))
        ).scalars()
    }
    assert {"user.create", "user.update", "user.delete"} <= actions


async def test_health_reports_groq_as_degraded_not_failure(
    client_factory, world, monkeypatch
) -> None:
    async def degraded(_settings):  # type: ignore[no-untyped-def]
        return "degraded"

    monkeypatch.setattr("app.services.admin_service.check_groq", degraded)
    client = await client_factory()
    await login(client, world.admin.email)

    response = await client.get("/api/v1/admin/system/health")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["checks"]["postgres"] == "ok"
    assert body["checks"]["chroma"] == "ok"
    assert body["checks"]["groq"] == "degraded"
    assert body["status"] == "degraded"
    assert body["queue_depth"] is None


async def test_health_includes_last_ingestion_failure(
    client_factory, world, session: AsyncSession, monkeypatch
) -> None:
    async def ok(_settings):  # type: ignore[no-untyped-def]
        return "not_configured"

    monkeypatch.setattr("app.services.admin_service.check_groq", ok)
    seeded = await seed_document(session, world)
    seeded.document.status = DocumentStatus.FAILED
    seeded.document.error_code = "PARSE_FAILED"
    seeded.document.error_detail = "The PDF is corrupt"
    await session.commit()

    client = await client_factory()
    await login(client, world.admin.email)
    response = await client.get("/api/v1/admin/system/health")
    assert response.status_code == 200, response.text
    failure = response.json()["last_ingestion_failure"]
    assert failure["document_id"] == str(seeded.document.id)
    assert failure["error_code"] == "PARSE_FAILED"
    assert failure["course_code"] == "SCI101"


async def test_audit_logs_are_append_only(client_factory, world) -> None:
    """There is deliberately no delete endpoint, and rows survive user deletion."""
    client = await client_factory()
    await login(client, world.admin.email)

    created = await client.post(
        "/api/v1/admin/users",
        json={"name": "Temporary", "email": "temporary@example.com", "role": "student"},
    )
    user_id = created.json()["id"]
    assert (await client.delete(f"/api/v1/admin/users/{user_id}")).status_code == 204

    response = await client.delete("/api/v1/admin/audit-logs")
    assert response.status_code in {404, 405}
