from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.vector_store import VectorStoreError
from app.services import ingestion_service
from app.services.ingestion_service import reconcile_vectors
from app.workers.ingest import reconcile_vectors as reconcile_worker
from tests.topic_utils import fake_vector_store, seed_document


async def test_reconcile_repairs_release_metadata_from_postgres(
    world, session: AsyncSession, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[
            {"title": "Released", "chunk_count": 2, "released": True},
            {"title": "Draft", "chunk_count": 2},
        ],
    )
    store = fake_vector_store(seeded)
    released_chunks = seeded.named("Released").chunks
    draft_chunks = seeded.named("Draft").chunks
    for chunk in released_chunks:
        store.records[chunk.vector_id]["metadata"]["released"] = False
    for chunk in draft_chunks:
        store.records[chunk.vector_id]["metadata"]["released"] = True

    monkeypatch.setattr(ingestion_service, "get_vector_store", lambda: store)
    checked, repaired = await reconcile_vectors()

    assert checked == 2
    assert repaired == 4
    assert all(store.records[chunk.vector_id]["metadata"]["released"] for chunk in released_chunks)
    assert not any(store.records[chunk.vector_id]["metadata"]["released"] for chunk in draft_chunks)

    checked_again, repaired_again = await reconcile_vectors()
    assert checked_again == 2
    assert repaired_again == 0


async def test_reconcile_survives_chroma_read_failure(
    world, session: AsyncSession, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    await seed_document(
        session,
        world,
        topic_specs=[{"title": "Offline", "chunk_count": 1, "released": True}],
    )

    class OfflineStore:
        def get(self, **kwargs: object) -> dict[str, object]:
            del kwargs
            raise VectorStoreError("Chroma is down")

        def update_metadata(self, **kwargs: object) -> None:
            del kwargs
            raise AssertionError("must not repair when reads fail")

    monkeypatch.setattr(ingestion_service, "get_vector_store", lambda: OfflineStore())
    checked, repaired = await reconcile_vectors()
    assert checked == 1
    assert repaired == 0


async def test_worker_reconcile_returns_report(world, session: AsyncSession, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Worker", "chunk_count": 1, "released": True}],
    )
    store = fake_vector_store(seeded)
    for chunk in seeded.named("Worker").chunks:
        store.records[chunk.vector_id]["metadata"]["released"] = False

    monkeypatch.setattr(ingestion_service, "get_vector_store", lambda: store)
    report = await reconcile_worker({})
    assert report == {"checked": 1, "repaired": 1}
