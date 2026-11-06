from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def _matches_condition(value: Any, condition: Any) -> bool:
    if not isinstance(condition, dict) or not condition:
        return value == condition
    for operator, operand in condition.items():
        if operator == "$eq":
            if value != operand:
                return False
        elif operator == "$in":
            if value not in operand:
                return False
        elif operator == "$ne":
            if value == operand:
                return False
        else:  # pragma: no cover - unsupported fake operator
            raise AssertionError(f"Unsupported filter operator {operator}")
    return True


def _matches_where(metadata: dict[str, Any], where: dict[str, Any]) -> bool:
    clauses: list[dict[str, Any]]
    if "$and" in where:
        clauses = list(where["$and"])
    else:
        clauses = [{key: value} for key, value in where.items()]
    for clause in clauses:
        for field, condition in clause.items():
            if not _matches_condition(metadata.get(field), condition):
                return False
    return True


class FakeEmbedder:
    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension
        self.encoded: list[str] = []

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def encode(
        self,
        texts: Sequence[str],
        *,
        batch_size: int | None = None,
    ) -> list[list[float]]:
        del batch_size
        self.encoded.extend(texts)
        return [
            [float((len(text) + index) % 17) / 17 for index in range(self.dimension)]
            for text in texts
        ]


class FakeVectorStore:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        self.records.clear()

    def upsert(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[dict[str, Any]],
    ) -> None:
        assert len(ids) == len(embeddings) == len(documents) == len(metadatas)
        for vector_id, embedding, text, metadata in zip(
            ids, embeddings, documents, metadatas, strict=True
        ):
            self.records[vector_id] = {
                "embedding": list(embedding),
                "document": text,
                "metadata": dict(metadata),
            }

    def query(
        self,
        vector: Sequence[float],
        *,
        where: dict[str, Any] | None = None,
        top_k: int = 12,
        include: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        del vector, include
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for vector_id, record in self.records.items():
            metadata = record["metadata"]
            if where and not _matches_where(metadata, where):
                continue
            ids.append(vector_id)
            documents.append(record["document"])
            metadatas.append(metadata)
            if len(ids) >= top_k:
                break
        return {
            "ids": [ids],
            "documents": [documents],
            "metadatas": [metadatas],
            "distances": [[0.0] * len(ids)],
        }

    def update_metadata(
        self,
        *,
        ids: Sequence[str],
        metadatas: Sequence[dict[str, Any]],
    ) -> None:
        for vector_id, metadata in zip(ids, metadatas, strict=True):
            if vector_id in self.records:
                self.records[vector_id]["metadata"].update(metadata)

    def get(
        self,
        *,
        ids: Sequence[str] | None = None,
        where: dict[str, Any] | None = None,
        include: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        del include
        selected = list(ids) if ids else list(self.records)
        result_ids: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for vector_id in selected:
            record = self.records.get(vector_id)
            if record is None:
                continue
            metadata = record["metadata"]
            if where and not _matches_where(metadata, where):
                continue
            result_ids.append(vector_id)
            metadatas.append(metadata)
        return {"ids": result_ids, "metadatas": metadatas}

    def delete(
        self,
        *,
        ids: Sequence[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None:
        if ids:
            for vector_id in ids:
                self.records.pop(vector_id, None)
        if where:
            document_id = where.get("document_id")
            for vector_id, record in list(self.records.items()):
                if document_id is None or record["metadata"].get("document_id") == document_id:
                    self.records.pop(vector_id, None)

    def count(self, *, where: dict[str, Any] | None = None) -> int:
        if where is None:
            return len(self.records)
        document_id = where.get("document_id")
        return sum(
            1
            for record in self.records.values()
            if document_id is None or record["metadata"].get("document_id") == document_id
        )
