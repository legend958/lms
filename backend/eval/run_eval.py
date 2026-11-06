"""Offline, deterministic RAG evaluation for the student study assistant.

The harness seeds an inline corpus into an in-memory SQLite database, indexes
the same chunks in a local cosine vector store, and runs the real
release-gated retriever plus citation validator. It needs neither Postgres,
Chroma, nor Groq, so it can run in CI on every prompt/retrieval change.

Metrics and gates (architecture §9.7):
  - hit@6: relevant source chunk present in the final top 6 (>= 0.90)
  - citation validity: answer citations all resolve to retrieved chunks (>= 0.98)
  - refusal accuracy: unreleased/out-of-scope/unsupported questions refused (>= 0.95)
  - leak rate: unreleased or out-of-scope chunk reaching the student path (= 0.0)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.roles import (
    CourseStatus,
    DocumentStatus,
    Role,
    TopicReleaseState,
    TopicSource,
    UserStatus,
)
from app.core.scope import CourseScope
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import Chunk, Course, Document, Topic, User
from app.rag.generator import detect_injection, validate_citations
from app.rag.retriever import Retriever

DEFAULT_SUITE = Path(__file__).with_name("rag_cases.yaml")
EMBEDDING_DIM = 256
TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    {
        "a",
        "about",
        "after",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "before",
        "below",
        "between",
        "both",
        "by",
        "can",
        "compare",
        "did",
        "do",
        "does",
        "during",
        "each",
        "explain",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "into",
        "is",
        "it",
        "its",
        "many",
        "must",
        "of",
        "on",
        "or",
        "other",
        "should",
        "show",
        "summarize",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
    }
)


def _stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 4 and token.endswith("ic"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(text: str) -> list[str]:
    return [
        _stem(token)
        for token in TOKEN_RE.findall(text.lower())
        if token not in STOPWORDS and len(token) > 1
    ]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


class HashingEmbedder:
    """Deterministic bag-of-words embedding with signed feature hashing."""

    dimension = EMBEDDING_DIM

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def encode(
        self,
        texts: list[str] | tuple[str, ...],
        *,
        batch_size: int | None = None,
    ) -> list[list[float]]:
        del batch_size
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            for token in _tokens(text):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimension
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                vector[index] += sign
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                vector = [value / norm for value in vector]
            vectors.append(vector)
        return vectors


def _matches_condition(value: Any, condition: Any) -> bool:
    if not isinstance(condition, dict) or not condition:
        return value == condition
    for operator, operand in condition.items():
        if operator == "$eq" and value != operand:
            return False
        if operator == "$in" and value not in operand:
            return False
        if operator == "$ne" and value == operand:
            return False
    return True


def _matches_where(metadata: dict[str, Any], where: dict[str, Any]) -> bool:
    clauses: list[dict[str, Any]]
    if "$and" in where:
        clauses = list(where["$and"])
    else:
        clauses = [{key: value} for key, value in where.items()]
    for clause in clauses:
        for field_name, condition in clause.items():
            if not _matches_condition(metadata.get(field_name), condition):
                return False
    return True


class EvalVectorStore:
    """In-memory cosine store with the same query shape as Chroma."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    def add(
        self,
        vector_id: str,
        *,
        document: str,
        metadata: dict[str, Any],
        embedding: list[float],
    ) -> None:
        self.records[vector_id] = {
            "document": document,
            "metadata": dict(metadata),
            "embedding": embedding,
        }

    def query(
        self,
        vector: list[float] | tuple[float, ...],
        *,
        where: dict[str, Any] | None = None,
        top_k: int = 12,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for vector_id, record in self.records.items():
            if where is not None and not _matches_where(record["metadata"], where):
                continue
            similarity = _cosine(list(vector), record["embedding"])
            scored.append((1.0 - similarity, vector_id, record))
        scored.sort(key=lambda item: item[0])
        selected = scored[:top_k]

        response: dict[str, Any] = {
            "ids": [[vector_id for _, vector_id, _ in selected]],
            "documents": [[record["document"] for _, _, record in selected]],
            "metadatas": [[record["metadata"] for _, _, record in selected]],
            "distances": [[distance for distance, _, _ in selected]],
        }
        if include and "embeddings" in include:
            response["embeddings"] = [[record["embedding"] for _, _, record in selected]]
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


@dataclass
class Runtime:
    admin: User
    courses: dict[str, Course]
    store: EvalVectorStore
    embedder: HashingEmbedder
    key_by_vector_id: dict[str, str]
    visible_by_course: dict[str, set[str]]


@dataclass
class Metrics:
    total: int = 0
    hit_num: int = 0
    hit_den: int = 0
    citation_num: int = 0
    refusal_num: int = 0
    refusal_den: int = 0
    injection_num: int = 0
    injection_den: int = 0
    leak_cases: set[str] = field(default_factory=set)
    failures: list[str] = field(default_factory=list)

    @property
    def hit_at_6(self) -> float:
        return self.hit_num / self.hit_den if self.hit_den else 1.0

    @property
    def citation_validity(self) -> float:
        return self.citation_num / self.hit_den if self.hit_den else 1.0

    @property
    def refusal_accuracy(self) -> float:
        return self.refusal_num / self.refusal_den if self.refusal_den else 1.0

    @property
    def leak_rate(self) -> float:
        return len(self.leak_cases) / self.total if self.total else 0.0


async def _seed(suite: dict[str, Any], session: AsyncSession) -> Runtime:
    admin = User(
        name="Eval Admin",
        email="eval@example.com",
        password_hash=hash_password("eval-password-not-used"),
        role=Role.ADMIN,
        status=UserStatus.ACTIVE,
    )
    session.add(admin)
    await session.flush()

    courses: dict[str, Course] = {}
    for spec in suite["courses"]:
        course = Course(
            code=spec["code"],
            title=spec["title"],
            term="eval",
            created_by=admin.id,
            status=CourseStatus.ACTIVE,
        )
        session.add(course)
        await session.flush()
        courses[spec["key"]] = course

    documents: dict[str, Document] = {}
    for spec in suite["documents"]:
        course = courses[spec["course"]]
        document = Document(
            course_id=course.id,
            uploaded_by=admin.id,
            filename=spec["filename"],
            checksum=hashlib.sha256(spec["key"].encode()).hexdigest(),
            size_bytes=1024,
            page_count=100,
            status=DocumentStatus(spec.get("status", "ready")),
            pipeline_version=1,
            topic_count=0,
            topics_source=TopicSource.LLM,
            deleted_at=datetime.now(UTC) if spec.get("deleted") else None,
        )
        session.add(document)
        await session.flush()
        documents[spec["key"]] = document

    embedder = HashingEmbedder()
    store = EvalVectorStore()
    key_by_vector_id: dict[str, str] = {}
    visible_by_course: dict[str, set[str]] = {key: set() for key in courses}
    chunk_index = 0

    for order_index, spec in enumerate(suite["topics"]):
        document = documents[spec["document"]]
        page_start = int(spec.get("page_start", 1))
        chunks_spec: list[dict[str, Any]] = list(spec.get("chunks", []))
        page_end = max(
            [int(chunk.get("page", page_start)) for chunk in chunks_spec] or [page_start]
        )
        released = bool(spec.get("released", False))
        topic = Topic(
            document_id=document.id,
            course_id=document.course_id,
            title=spec["title"],
            summary=spec["title"],
            page_start=page_start,
            page_end=page_end,
            order_index=order_index,
            release_state=(TopicReleaseState.RELEASED if released else TopicReleaseState.DRAFT),
            released_by=admin.id if released else None,
            released_at=datetime.now(UTC) if released else None,
            needs_review=False,
        )
        session.add(topic)
        await session.flush()

        for chunk_spec in chunks_spec:
            page = int(chunk_spec.get("page", page_start))
            vector_id = f"{document.id}:{chunk_index}"
            chunk = Chunk(
                vector_id=vector_id,
                document_id=document.id,
                course_id=document.course_id,
                topic_id=topic.id,
                chunk_index=chunk_index,
                text=chunk_spec["text"],
                page_start=page,
                page_end=page,
                section_title=spec["title"],
                token_count=len(chunk_spec["text"].split()),
                released=released,
            )
            session.add(chunk)
            key_by_vector_id[vector_id] = chunk_spec["key"]
            chunk_index += 1

            # Deliberately stale Chroma metadata for some draft topics.
            indexed_released = bool(spec.get("index_released", released))
            embedding = embedder.encode([chunk_spec["text"]])[0]
            store.add(
                vector_id,
                document=chunk_spec["text"],
                metadata={
                    "course_id": str(document.course_id),
                    "document_id": str(document.id),
                    "topic_id": str(topic.id),
                    "page_start": page,
                    "page_end": page,
                    "section_title": spec["title"],
                    "released": indexed_released,
                    "pipeline_version": 1,
                    "chunk_index": chunk_index - 1,
                },
                embedding=embedding,
            )

            if released and document.status is DocumentStatus.READY and document.deleted_at is None:
                course_key = next(
                    key for key, course in courses.items() if course.id == document.course_id
                )
                visible_by_course[course_key].add(chunk_spec["key"])

        document.topic_count += 1

    await session.commit()
    return Runtime(
        admin=admin,
        courses=courses,
        store=store,
        embedder=embedder,
        key_by_vector_id=key_by_vector_id,
        visible_by_course=visible_by_course,
    )


def _record(
    metrics: Metrics,
    case_id: str,
    passed: bool,
    reason: str,
) -> None:
    if not passed:
        metrics.failures.append(f"{case_id}: {reason}")


async def _evaluate(
    suite: dict[str, Any],
    session: AsyncSession,
    runtime: Runtime,
) -> Metrics:
    settings = get_settings()
    retriever = Retriever(embedder=runtime.embedder, vector_store=runtime.store, settings=settings)
    metrics = Metrics()

    for case in suite["cases"]:
        metrics.total += 1
        case_id = str(case["id"])
        scope_keys = [str(key) for key in case.get("scope", [])]
        course_ids = frozenset(runtime.courses[key].id for key in scope_keys)
        scope = CourseScope(user_id=runtime.admin.id, role=Role.STUDENT, course_ids=course_ids)
        course_id = None
        if case.get("course"):
            course_id = runtime.courses[str(case["course"])].id

        result = await retriever.retrieve(
            session,
            question=str(case["question"]),
            scope=scope,
            course_id=course_id,
        )
        retrieved = [runtime.key_by_vector_id[chunk.vector_id] for chunk in result.chunks]
        visible: set[str] = set()
        for key in scope_keys:
            visible |= runtime.visible_by_course[key]

        leaked = [key for key in retrieved if key not in visible]
        if leaked:
            metrics.leak_cases.add(case_id)

        expect = str(case.get("expect", "answer"))
        if expect == "refusal":
            metrics.refusal_den += 1
            if case.get("citation_mode") == "invalid" and result.chunks:
                raw = "This is an unsupported answer [99]."
                _, citations, refused = validate_citations(raw, result.chunks)
            else:
                citations = ()
                refused = result.refusal
            metrics.refusal_num += int(refused and not citations)
            _record(
                metrics,
                case_id,
                refused and not citations,
                f"expected refusal, got {len(result.chunks)} fetched chunk(s)",
            )
            continue

        metrics.hit_den += 1
        relevant = [str(key) for key in case.get("relevant", [])]
        if case.get("cross"):
            hit = all(key in retrieved for key in relevant)
        else:
            hit = any(key in retrieved for key in relevant)
        metrics.hit_num += int(hit)
        _record(metrics, case_id, hit, f"relevant {relevant} not in top 6 {retrieved}")

        indexes: list[int] = []
        for key in relevant:
            if key in retrieved:
                index = retrieved.index(key) + 1
                if index not in indexes:
                    indexes.append(index)
        if indexes:
            raw = "Supported answer " + "".join(f"[{index}]" for index in indexes) + "."
        else:
            raw = "No supporting source [99]."
        _, citations, refused = validate_citations(raw, result.chunks)
        cited_keys = {
            runtime.key_by_vector_id[chunk.vector_id]
            for chunk in result.chunks
            if chunk.chunk_id in {citation.chunk_id for citation in citations}
        }
        citation_ok = not refused and bool(citations) and cited_keys <= visible
        metrics.citation_num += int(citation_ok)
        _record(metrics, case_id, citation_ok, "answer did not produce valid citations")

        if case.get("expect_injection"):
            metrics.injection_den += 1
            flagged = detect_injection(result.chunks)
            metrics.injection_num += int(flagged)
            _record(metrics, case_id, flagged, "injection phrase was not flagged")

    return metrics


def _report(metrics: Metrics, thresholds: dict[str, Any]) -> bool:
    hit_gate = float(thresholds["hit_at_6"])
    citation_gate = float(thresholds["citation_validity"])
    refusal_gate = float(thresholds["refusal_accuracy"])
    leak_gate = float(thresholds["leak_rate"])

    rows = [
        ("hit@6", metrics.hit_at_6, f">= {hit_gate:.2f}", metrics.hit_at_6 >= hit_gate),
        (
            "citation validity",
            metrics.citation_validity,
            f">= {citation_gate:.2f}",
            metrics.citation_validity >= citation_gate,
        ),
        (
            "refusal accuracy",
            metrics.refusal_accuracy,
            f">= {refusal_gate:.2f}",
            metrics.refusal_accuracy >= refusal_gate,
        ),
        (
            "leak rate",
            metrics.leak_rate,
            f"<= {leak_gate:.2f}",
            metrics.leak_rate <= leak_gate,
        ),
    ]

    print(f"RAG eval: {metrics.total} cases")
    print(f"{'metric':<20}{'value':>8}  {'gate':<12}status")
    for name, value, gate, passed in rows:
        print(f"{name:<20}{value:>8.3f}  {gate:<12}{'PASS' if passed else 'FAIL'}")
    if metrics.injection_den:
        print(f"injection flagged: {metrics.injection_num}/{metrics.injection_den} (informational)")
    if metrics.failures:
        print("\nFailures:")
        for failure in metrics.failures:
            print(f"  - {failure}")
    if metrics.leak_cases:
        print(f"\nLeak cases: {', '.join(sorted(metrics.leak_cases))}")

    return all(passed for _, _, _, passed in rows)


async def run(suite_path: Path) -> bool:
    text = await asyncio.to_thread(suite_path.read_text, encoding="utf-8")
    suite = yaml.safe_load(text)
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with maker() as session:
            runtime = await _seed(suite, session)
            metrics = await _evaluate(suite, session, runtime)
    finally:
        await engine.dispose()

    return _report(metrics, suite.get("thresholds", {}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    args = parser.parse_args()
    suite_path: Path = args.suite
    if not suite_path.exists():
        candidate = DEFAULT_SUITE.parent / suite_path
        if candidate.exists():
            suite_path = candidate
    if not suite_path.exists():
        print(f"Suite not found: {suite_path}", file=sys.stderr)
        return 2
    return 0 if asyncio.run(run(suite_path)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
