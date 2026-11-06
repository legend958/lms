"""Helpers for Phase 6 study-assistant tests."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from app.main import app
from app.rag.embedder import get_embedder
from app.rag.generator import (
    REFUSAL_TEXT,
    GeneratedAnswer,
    detect_injection,
    get_answer_generator,
    validate_citations,
)
from app.rag.retriever import RetrievedChunk
from app.rag.vector_store import get_vector_store
from tests.fakes import _matches_where
from tests.topic_utils import SeededDocument


class StubVectorStore:
    """Deterministic vector store with explicit distances and embeddings."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.queries: list[dict[str, Any]] = []

    def add(
        self,
        vector_id: str,
        *,
        document: str,
        metadata: dict[str, Any],
        distance: float = 0.0,
        embedding: list[float] | None = None,
    ) -> None:
        self.records[vector_id] = {
            "document": document,
            "metadata": dict(metadata),
            "distance": distance,
            "embedding": embedding or [1.0, 0.0],
        }

    def query(
        self,
        vector: Sequence[float],
        *,
        where: dict[str, Any] | None = None,
        top_k: int = 12,
        include: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        del vector
        self.queries.append({"where": where, "top_k": top_k, "include": include})
        selected: list[tuple[str, dict[str, Any]]] = []
        for vector_id, record in self.records.items():
            if where is not None and not _matches_where(record["metadata"], where):
                continue
            selected.append((vector_id, record))
            if len(selected) >= top_k:
                break

        response: dict[str, Any] = {
            "ids": [[vector_id for vector_id, _ in selected]],
            "documents": [[record["document"] for _, record in selected]],
            "metadatas": [[record["metadata"] for _, record in selected]],
            "distances": [[record["distance"] for _, record in selected]],
        }
        if include and "embeddings" in include:
            response["embeddings"] = [[record["embedding"] for _, record in selected]]
        return response

    def upsert(self, **kwargs: object) -> None:
        del kwargs

    def update_metadata(self, **kwargs: object) -> None:
        del kwargs

    def get(self, **kwargs: object) -> dict[str, Any]:
        del kwargs
        return {"ids": [], "metadatas": []}

    def delete(self, **kwargs: object) -> None:
        del kwargs

    def count(self, **kwargs: object) -> int:
        del kwargs
        return len(self.records)


def store_for_document(
    seeded: SeededDocument,
    *,
    distance: float = 0.0,
    force_released: bool = False,
) -> StubVectorStore:
    store = StubVectorStore()
    for topic in seeded.topics:
        for chunk in topic.chunks:
            store.add(
                chunk.vector_id,
                document=chunk.text,
                metadata={
                    "course_id": str(chunk.course_id),
                    "document_id": str(chunk.document_id),
                    "topic_id": str(chunk.topic_id),
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "section_title": chunk.section_title or "",
                    "released": True if force_released else chunk.released,
                    "pipeline_version": 1,
                    "chunk_index": chunk.chunk_index,
                },
                distance=distance,
                embedding=[1.0, float(chunk.chunk_index)],
            )
    return store


class FakeAnswerGenerator:
    """Run the real citation validator over a scripted model response."""

    def __init__(
        self,
        raw: str = "Answer grounded in the released material [1].",
        *,
        error: Exception | None = None,
    ) -> None:
        self.raw = raw
        self.error = error
        self.calls: list[tuple[str, tuple[RetrievedChunk, ...]]] = []

    async def answer(
        self,
        question: str,
        chunks: Sequence[RetrievedChunk],
    ) -> GeneratedAnswer:
        self.calls.append((question, tuple(chunks)))
        if self.error is not None:
            raise self.error
        if not chunks:
            return GeneratedAnswer(
                answer=REFUSAL_TEXT,
                citations=(),
                refusal=True,
                injection_flag=False,
                tokens_in=0,
                tokens_out=0,
            )
        answer, citations, refusal = validate_citations(self.raw, chunks)
        return GeneratedAnswer(
            answer=answer,
            citations=citations,
            refusal=refusal,
            injection_flag=detect_injection(chunks),
            tokens_in=max(1, len(question) // 4),
            tokens_out=max(1, len(answer) // 4),
        )


@contextmanager
def assistant_overrides(
    *,
    embedder: Any,
    vector_store: Any,
    generator: Any,
) -> Iterator[None]:
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_vector_store] = lambda: vector_store
    app.dependency_overrides[get_answer_generator] = lambda: generator
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_embedder, None)
        app.dependency_overrides.pop(get_vector_store, None)
        app.dependency_overrides.pop(get_answer_generator, None)


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        events.append((event, json.loads(data)))
    return events


def citation_ids(seeded: SeededDocument, *, released: bool | None = None) -> set[uuid.UUID]:
    result: set[uuid.UUID] = set()
    for topic in seeded.topics:
        for chunk in topic.chunks:
            if released is None or chunk.released is released:
                result.add(chunk.id)
    return result
