"""ChromaDB adapter.

Chroma is always reached over HTTP. Tests substitute an in-memory implementation
of the same protocol, which keeps the suite independent of Docker.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Protocol, cast

from app.core.config import get_settings

DEFAULT_INCLUDE = ["documents", "metadatas", "distances"]


def _normalize_where(where: dict[str, Any] | None) -> dict[str, Any] | None:
    """Translate a field map into Chroma's single-operator filter shape.

    Chroma accepts ``{"field": value}`` for one condition and
    ``{"$and": [{"field": value}, ...]}`` for several. Callers should not have
    to know that detail, so a plain multi-key map is expanded here.
    """
    if not where or len(where) <= 1:
        return where
    if any(key.startswith("$") for key in where):
        return where
    return {"$and": [{key: value} for key, value in where.items()]}


class VectorStoreError(RuntimeError):
    """Raised when a vector operation fails."""


class VectorStore(Protocol):
    def upsert(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[dict[str, Any]],
    ) -> None: ...

    def query(
        self,
        vector: Sequence[float],
        *,
        where: dict[str, Any] | None = None,
        top_k: int = 12,
        include: Sequence[str] | None = None,
    ) -> dict[str, Any]: ...

    def update_metadata(
        self,
        *,
        ids: Sequence[str],
        metadatas: Sequence[dict[str, Any]],
    ) -> None: ...

    def get(
        self,
        *,
        ids: Sequence[str] | None = None,
        where: dict[str, Any] | None = None,
        include: Sequence[str] | None = None,
    ) -> dict[str, Any]: ...

    def delete(
        self,
        *,
        ids: Sequence[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None: ...

    def count(self, *, where: dict[str, Any] | None = None) -> int: ...


class ChromaVectorStore:
    def __init__(self, *, host: str, port: int, collection_name: str) -> None:
        self.host = host
        self.port = port
        self.collection_name = collection_name
        self._client: Any | None = None
        self._collection: Any | None = None

    def _get_collection(self) -> Any:
        if self._collection is not None:
            return self._collection
        try:
            import chromadb

            client = chromadb.HttpClient(host=self.host, port=self.port)
            try:
                collection = client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
            except (TypeError, ValueError):
                collection = client.get_or_create_collection(
                    name=self.collection_name,
                    configuration={"hnsw": {"space": "cosine"}},
                )
        except Exception as exc:
            raise VectorStoreError("Could not connect to the vector store") from exc
        self._client = client
        self._collection = collection
        return collection

    def upsert(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[dict[str, Any]],
    ) -> None:
        if not ids:
            return
        try:
            self._get_collection().upsert(
                ids=list(ids),
                embeddings=[list(vector) for vector in embeddings],
                documents=list(documents),
                metadatas=[dict(metadata) for metadata in metadatas],
            )
        except Exception as exc:
            raise VectorStoreError("Could not write vectors") from exc

    def query(
        self,
        vector: Sequence[float],
        *,
        where: dict[str, Any] | None = None,
        top_k: int = 12,
        include: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        try:
            return cast(
                dict[str, Any],
                self._get_collection().query(
                    query_embeddings=[list(vector)],
                    n_results=top_k,
                    where=_normalize_where(where),
                    include=list(include or DEFAULT_INCLUDE),
                ),
            )
        except Exception as exc:
            raise VectorStoreError("Could not query vectors") from exc

    def update_metadata(
        self,
        *,
        ids: Sequence[str],
        metadatas: Sequence[dict[str, Any]],
    ) -> None:
        if not ids:
            return
        try:
            self._get_collection().update(
                ids=list(ids),
                metadatas=[dict(metadata) for metadata in metadatas],
            )
        except Exception as exc:
            raise VectorStoreError("Could not update vector metadata") from exc

    def get(
        self,
        *,
        ids: Sequence[str] | None = None,
        where: dict[str, Any] | None = None,
        include: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        try:
            return cast(
                dict[str, Any],
                self._get_collection().get(
                    ids=list(ids) if ids else None,
                    where=_normalize_where(where),
                    include=list(include or ["metadatas"]),
                ),
            )
        except Exception as exc:
            raise VectorStoreError("Could not read vector metadata") from exc

    def delete(
        self,
        *,
        ids: Sequence[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None:
        if not ids and not where:
            raise VectorStoreError("A vector delete requires ids or a where filter")
        try:
            self._get_collection().delete(
                ids=list(ids) if ids else None,
                where=_normalize_where(where),
            )
        except Exception as exc:
            raise VectorStoreError("Could not delete vectors") from exc

    def count(self, *, where: dict[str, Any] | None = None) -> int:
        try:
            collection = self._get_collection()
            if where is None:
                return int(collection.count())
            result = collection.get(where=_normalize_where(where), include=[])
            return len(result.get("ids", []))
        except Exception as exc:
            raise VectorStoreError("Could not count vectors") from exc


@lru_cache(maxsize=1)
def get_vector_store() -> VectorStore:
    settings = get_settings()
    return ChromaVectorStore(
        host=settings.chroma_host,
        port=settings.chroma_port,
        collection_name=settings.chroma_collection,
    )
