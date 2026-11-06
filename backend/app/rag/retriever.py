"""Release-gated retrieval for the student study assistant.

The vector query already carries ``released=true`` and the enrolled course set,
but every candidate is resolved back to Postgres before it reaches the prompt.
That second check is deliberate: Chroma metadata can lag after a failed update,
and a student must never see a chunk whose topic is draft or deleted.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from app.core.config import Settings
from app.core.scope import CourseScope
from app.db.repositories import rag as rag_repo
from app.rag.access import student_vector_where
from app.rag.embedder import TextEmbedder
from app.rag.vector_store import VectorStore

logger = structlog.get_logger(__name__)

QUERY_INCLUDE = ["documents", "metadatas", "distances", "embeddings"]


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    vector_id: str
    document_id: uuid.UUID
    course_id: uuid.UUID
    topic_id: uuid.UUID
    title: str
    text: str
    page: int
    section_title: str | None
    similarity: float
    embedding: tuple[float, ...] | None = None


@dataclass(frozen=True)
class RetrievalResult:
    chunks: tuple[RetrievedChunk, ...]
    max_similarity: float

    @property
    def refusal(self) -> bool:
        return not self.chunks


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def mmr_select(
    candidates: list[RetrievedChunk],
    query_embedding: tuple[float, ...],
    *,
    final_k: int,
    lambda_: float,
) -> list[RetrievedChunk]:
    """Greedy maximal marginal relevance over already-filtered candidates."""
    if final_k <= 0 or not candidates:
        return []

    remaining = list(candidates)
    selected: list[RetrievedChunk] = []
    while remaining and len(selected) < final_k:
        best: RetrievedChunk | None = None
        best_score = -math.inf
        for candidate in remaining:
            relevance = candidate.similarity
            if query_embedding and candidate.embedding:
                relevance = max(relevance, _cosine(query_embedding, candidate.embedding))
            redundancy = 0.0
            if selected:
                redundancy = max(
                    (
                        _cosine(candidate.embedding, chosen.embedding)
                        if candidate.embedding and chosen.embedding
                        else 0.0
                    )
                    for chosen in selected
                )
            score = lambda_ * relevance - (1.0 - lambda_) * redundancy
            if score > best_score:
                best_score = score
                best = candidate
        if best is None:  # pragma: no cover - defensive
            break
        selected.append(best)
        remaining.remove(best)
    return selected


def _first(value: Any) -> list[Any]:
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, list):
            return first
    return []


class Retriever:
    def __init__(
        self,
        *,
        embedder: TextEmbedder,
        vector_store: VectorStore,
        settings: Settings,
    ) -> None:
        self.embedder = embedder
        self.vector_store = vector_store
        self.settings = settings

    async def retrieve(
        self,
        session: Any,
        *,
        question: str,
        scope: CourseScope,
        course_id: uuid.UUID | None = None,
        topic_id: uuid.UUID | None = None,
    ) -> RetrievalResult:
        if not scope.course_ids:
            return RetrievalResult(chunks=(), max_similarity=0.0)
        if course_id is not None and course_id not in scope.course_ids:
            return RetrievalResult(chunks=(), max_similarity=0.0)

        vectors = await asyncio.to_thread(self.embedder.encode, [question])
        if not vectors:
            return RetrievalResult(chunks=(), max_similarity=0.0)
        query_embedding = tuple(float(value) for value in vectors[0])

        course_ids = {course_id} if course_id is not None else scope.course_ids
        where = student_vector_where(course_ids, topic_id=topic_id)
        response = await asyncio.to_thread(
            self.vector_store.query,
            query_embedding,
            where=where,
            top_k=self.settings.chroma_top_k,
            include=QUERY_INCLUDE,
        )

        ids = _first(response.get("ids") if isinstance(response, dict) else None)
        distances = _first(response.get("distances") if isinstance(response, dict) else None)
        embeddings_raw = _first(response.get("embeddings") if isinstance(response, dict) else None)

        similarities: dict[str, float] = {}
        embeddings: dict[str, tuple[float, ...]] = {}
        for index, vector_id in enumerate(ids):
            distance = float(distances[index]) if index < len(distances) else 1.0
            similarity = max(0.0, min(1.0, 1.0 - distance))
            similarities[str(vector_id)] = similarity
            if index < len(embeddings_raw) and embeddings_raw[index] is not None:
                embeddings[str(vector_id)] = tuple(float(value) for value in embeddings_raw[index])

        ranked = sorted(similarities.items(), key=lambda item: item[1], reverse=True)
        above_threshold = [
            (vector_id, similarity)
            for vector_id, similarity in ranked
            if similarity >= self.settings.chroma_min_similarity
        ]
        if not above_threshold:
            return RetrievalResult(chunks=(), max_similarity=0.0)

        visible = await rag_repo.resolve_visible_chunks(
            session,
            vector_ids=[vector_id for vector_id, _ in above_threshold],
            scope=scope,
            course_id=course_id,
            topic_id=topic_id,
        )

        candidates: list[RetrievedChunk] = []
        for vector_id, similarity in above_threshold:
            row = visible.get(vector_id)
            if row is None:
                # Chroma said released, Postgres disagrees. Fail closed.
                logger.warning("assistant_candidate_hidden", vector_id=vector_id)
                continue
            candidates.append(
                RetrievedChunk(
                    chunk_id=row.chunk_id,
                    vector_id=vector_id,
                    document_id=row.document_id,
                    course_id=row.course_id,
                    topic_id=row.topic_id,
                    title=row.title,
                    text=row.text,
                    page=row.page,
                    section_title=row.section_title,
                    similarity=similarity,
                    embedding=embeddings.get(vector_id),
                )
            )

        if not candidates:
            return RetrievalResult(chunks=(), max_similarity=0.0)

        selected = mmr_select(
            candidates,
            query_embedding,
            final_k=self.settings.chroma_final_k,
            lambda_=self.settings.rag_mmr_lambda,
        )
        return RetrievalResult(
            chunks=tuple(selected),
            max_similarity=max(candidate.similarity for candidate in candidates),
        )
