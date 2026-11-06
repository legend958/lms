from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services.vector_rebuild_service import rebuild_vectors
from tests.fakes import FakeEmbedder, FakeVectorStore
from tests.topic_utils import seed_document


async def test_rebuild_replaces_stale_index_with_all_chunks(world, session: AsyncSession) -> None:
    seeded = await seed_document(
        session,
        world,
        topic_specs=[
            {"title": "Released", "chunk_count": 2, "released": True},
            {"title": "Draft", "chunk_count": 2},
        ],
    )
    store = FakeVectorStore()
    store.records["stale-vector"] = {"embedding": [], "document": "old", "metadata": {}}
    embedder = FakeEmbedder()

    written = await rebuild_vectors(
        session,
        settings=get_settings(),
        embedder=embedder,
        vector_store=store,
    )

    assert written == 4
    assert "stale-vector" not in store.records
    assert set(store.records) == {
        chunk.vector_id for item in seeded.topics for chunk in item.chunks
    }

    released = seeded.named("Released").chunks
    draft = seeded.named("Draft").chunks
    assert all(store.records[chunk.vector_id]["metadata"]["released"] for chunk in released)
    assert not any(store.records[chunk.vector_id]["metadata"]["released"] for chunk in draft)
    assert all(
        store.records[chunk.vector_id]["metadata"]["topic_id"] == str(chunk.topic_id)
        for chunk in released
    )
    assert sorted(embedder.encoded) == sorted(
        chunk.text for item in seeded.topics for chunk in item.chunks
    )


async def test_rebuild_is_a_noop_when_database_has_no_chunks(
    session: AsyncSession,
) -> None:
    store = FakeVectorStore()
    store.records["keep-me"] = {"embedding": [], "document": "old", "metadata": {}}

    written = await rebuild_vectors(
        session,
        settings=get_settings(),
        embedder=FakeEmbedder(),
        vector_store=store,
    )

    assert written == 0
    assert "keep-me" in store.records
