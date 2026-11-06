from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers import documents as documents_router
from app.core.config import get_settings
from app.core.roles import DocumentStatus
from app.db.repositories import documents as documents_repo
from tests.conftest import login
from tests.pdf_utils import make_encrypted_pdf_bytes, make_pdf_bytes


async def _upload(
    client: AsyncClient,
    course_id: object,
    *,
    data: bytes | None = None,
    filename: str = "lesson.pdf",
    content_type: str = "application/pdf",
):
    return await client.post(
        "/api/v1/documents",
        files={"file": (filename, data or make_pdf_bytes(), content_type)},
        data={"course_id": str(course_id)},
    )


async def test_teacher_can_upload_and_poll_document(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.teacher.email)

    response = await _upload(client, world.course_a.id)
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["status"] == "queued"
    document_id = payload["document_id"]

    listing = await client.get(f"/api/v1/documents?course_id={world.course_a.id}")
    assert listing.status_code == 200, listing.text
    items = listing.json()
    assert len(items) == 1
    assert items[0]["id"] == document_id
    assert items[0]["course_code"] == "SCI101"
    assert items[0]["chunk_count"] == 0
    assert items[0]["status"] == "queued"

    detail = await client.get(f"/api/v1/documents/{document_id}")
    assert detail.status_code == 200
    assert detail.json()["filename"] == "lesson.pdf"

    status = await client.get(f"/api/v1/documents/{document_id}/status")
    assert status.status_code == 200, status.text
    assert status.json()["stage"] == "queued"
    assert status.json()["progress_pct"] == 0
    assert status.json()["error_code"] is None


async def test_duplicate_upload_returns_existing_document_id(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.teacher.email)
    data = make_pdf_bytes()

    first = await _upload(client, world.course_a.id, data=data)
    assert first.status_code == 202
    second = await _upload(client, world.course_a.id, data=data)
    assert second.status_code == 409
    assert second.json()["document_id"] == first.json()["document_id"]


async def test_students_and_non_owner_teachers_are_rejected(client_factory, world) -> None:
    student = await client_factory()
    await login(student, world.student.email)
    assert (await _upload(student, world.course_a.id)).status_code == 403
    assert (await student.get("/api/v1/documents")).status_code == 403

    teacher_b = await client_factory()
    await login(teacher_b, world.teacher_b.email)
    assert (await _upload(teacher_b, world.course_a.id)).status_code == 404
    assert (
        await teacher_b.get(f"/api/v1/documents?course_id={world.course_a.id}")
    ).status_code == 404


async def test_admin_can_upload_to_any_active_course(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.admin.email)

    response = await _upload(client, world.course_b.id)
    assert response.status_code == 202, response.text


async def test_enqueue_failure_marks_document_failed(
    client_factory, world, session: AsyncSession, monkeypatch
) -> None:
    client = await client_factory()
    await login(client, world.teacher.email)

    async def reject_enqueue(document_id: uuid.UUID) -> bool:
        del document_id
        return False

    monkeypatch.setattr(documents_router, "enqueue_ingest", reject_enqueue)
    monkeypatch.setattr(get_settings(), "ingest_queue", "arq")

    response = await _upload(client, world.course_a.id)
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["status"] == "failed"

    document = await documents_repo.get(session, uuid.UUID(payload["document_id"]))
    assert document is not None
    assert document.status is DocumentStatus.FAILED
    assert document.error_code == "QUEUE_UNAVAILABLE"

    status = await client.get(f"/api/v1/documents/{payload['document_id']}/status")
    assert status.status_code == 200, status.text
    assert status.json()["stage"] == "failed"
    assert status.json()["error_code"] == "QUEUE_UNAVAILABLE"


async def test_upload_validation_errors(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.teacher.email)

    assert (
        await _upload(client, world.course_a.id, data=b"not a pdf", filename="notes.txt")
    ).status_code == 422
    assert (
        await _upload(client, world.course_a.id, data=b"not a pdf", filename="notes.pdf")
    ).status_code == 422
    encrypted = await _upload(
        client,
        world.course_a.id,
        data=make_encrypted_pdf_bytes(),
        filename="secret.pdf",
    )
    assert encrypted.status_code == 422
    assert "Encrypted" in encrypted.json()["detail"]


async def test_reupload_after_soft_delete_is_allowed(client_factory, world) -> None:
    client = await client_factory()
    await login(client, world.teacher.email)
    data = make_pdf_bytes()

    first = await _upload(client, world.course_a.id, data=data)
    assert first.status_code == 202
    first_id = first.json()["document_id"]
    assert (await client.delete(f"/api/v1/documents/{first_id}")).status_code == 204

    second = await _upload(client, world.course_a.id, data=data)
    assert second.status_code == 202, second.text
    assert second.json()["document_id"] != first_id

    listing = await client.get(f"/api/v1/documents?course_id={world.course_a.id}")
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [second.json()["document_id"]]


async def test_retry_failed_document_and_delete(
    client_factory, world, session: AsyncSession
) -> None:
    client = await client_factory()
    await login(client, world.teacher.email)
    uploaded = await _upload(client, world.course_a.id)
    document_id = uploaded.json()["document_id"]

    queued_retry = await client.post(f"/api/v1/documents/{document_id}/retry")
    assert queued_retry.status_code == 409

    document = await documents_repo.get(session, uuid.UUID(document_id))
    assert document is not None
    document.status = DocumentStatus.FAILED
    document.error_code = "PARSE_FAILED"
    document.error_detail = "The PDF is corrupt"
    await session.commit()

    retried = await client.post(f"/api/v1/documents/{document_id}/retry")
    assert retried.status_code == 202, retried.text
    assert retried.json()["status"] == "queued"

    deleted = await client.delete(f"/api/v1/documents/{document_id}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/documents/{document_id}")).status_code == 404
