from __future__ import annotations

from typing import Any

from app.rag.vector_store import ChromaVectorStore, _normalize_where


class FakeCollection:
    def __init__(self) -> None:
        self.query_kwargs: dict[str, Any] = {}
        self.delete_kwargs: dict[str, Any] = {}
        self.get_kwargs: dict[str, Any] = {}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.query_kwargs = kwargs
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def delete(self, **kwargs: Any) -> None:
        self.delete_kwargs = kwargs

    def get(self, **kwargs: Any) -> dict[str, Any]:
        self.get_kwargs = kwargs
        return {"ids": ["one", "two"]}

    def count(self) -> int:
        return 2


def test_normalize_where_expands_multi_field_maps() -> None:
    assert _normalize_where({"document_id": "d"}) == {"document_id": "d"}
    assert _normalize_where({"$and": [{"released": True}, {"course_id": "c"}]}) == {
        "$and": [{"released": True}, {"course_id": "c"}]
    }
    assert _normalize_where({"course_id": "c", "released": True}) == {
        "$and": [{"course_id": "c"}, {"released": True}]
    }


def test_chroma_adapter_expands_filters_for_query_delete_and_count() -> None:
    store = ChromaVectorStore(host="localhost", port=8001, collection_name="test")
    collection = FakeCollection()
    store._collection = collection  # type: ignore[attr-defined]

    where = {"course_id": "c", "released": True}
    expected = {"$and": [{"course_id": "c"}, {"released": True}]}

    store.query([0.0, 0.0], where=where)
    store.delete(where=where)
    assert store.count(where=where) == 2

    assert collection.query_kwargs["where"] == expected
    assert collection.delete_kwargs["where"] == expected
    assert collection.get_kwargs["where"] == expected
