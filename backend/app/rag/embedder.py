"""Local sentence-transformer embeddings.

The model is loaded lazily. The ingestion worker loads it on first use, and the
API warms it in a background startup task so the first study-assistant question
does not pay the model-load cost.
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Protocol, cast

from app.core.config import get_settings


class EmbeddingError(RuntimeError):
    """Raised when the embedding model cannot load or encode text."""


class TextEmbedder(Protocol):
    def count_tokens(self, text: str) -> int: ...

    def encode(
        self,
        texts: Sequence[str],
        *,
        batch_size: int | None = None,
    ) -> list[list[float]]: ...


def _token_ids(tokenizer: Any, text: str) -> list[int]:
    try:
        return list(tokenizer(text, add_special_tokens=False, verbose=False)["input_ids"])
    except TypeError:
        # Older tokenizer wrappers do not accept ``verbose``.
        return list(tokenizer(text, add_special_tokens=False)["input_ids"])


def _windows_for_text(model: Any, text: str, max_tokens: int) -> list[str]:
    """Split text into token windows that fit the model context.

    The chunker deliberately builds ~600-token chunks, while MiniLM was trained
    on 256-token inputs. Embedding only the first 256 tokens would make the rest
    of a chunk invisible to retrieval, so long chunks are embedded as overlapping
    windows and their vectors are mean-pooled by the caller.
    """
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is None or max_tokens <= 0:
        return [text]
    try:
        token_ids = _token_ids(tokenizer, text)
    except Exception:
        return [text]
    if len(token_ids) <= max_tokens:
        return [text]

    stride = max(1, max_tokens // 2)
    windows: list[str] = []
    for start in range(0, len(token_ids), stride):
        window_ids = token_ids[start : start + max_tokens]
        if not window_ids:
            break
        try:
            windows.append(tokenizer.decode(window_ids, skip_special_tokens=True))
        except Exception:
            # A decode failure should not lose the chunk; fall back to a coarse
            # character slice and let the model handle it.
            windows.append(text[start : start + max_tokens])
        if start + max_tokens >= len(token_ids):
            break
    return windows or [text]


def _mean_normalize(vectors: list[list[float]], dimension: int) -> list[float]:
    totals = [0.0] * dimension
    for vector in vectors:
        if len(vector) != dimension:
            raise EmbeddingError(
                f"Embedding vector has {len(vector)} dimensions, expected {dimension}"
            )
        for index, value in enumerate(vector):
            totals[index] += value
    norm = math.sqrt(sum(value * value for value in totals)) or 1.0
    return [value / norm for value in totals]


class SentenceTransformerEmbedder:
    def __init__(self, *, model_name: str, dimension: int, batch_size: int) -> None:
        self.model_name = model_name
        self.dimension = dimension
        self.batch_size = batch_size
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(self.model_name)
        except Exception as exc:  # pragma: no cover - depends on local model cache
            raise EmbeddingError(f"Could not load embedding model {self.model_name}") from exc

        dimension_getter = getattr(model, "get_embedding_dimension", None) or getattr(
            model, "get_sentence_embedding_dimension", None
        )
        if dimension_getter is None:
            raise EmbeddingError(f"Embedding model {self.model_name} has no dimension metadata")
        actual = int(dimension_getter())
        if actual != self.dimension:
            raise EmbeddingError(
                f"Embedding model returned {actual} dimensions, expected {self.dimension}"
            )
        self._model = model
        return model

    def count_tokens(self, text: str) -> int:
        model = self._load()
        tokenizer = getattr(model, "tokenizer", None)
        if tokenizer is None:
            return len(text.split())
        try:
            return len(_token_ids(tokenizer, text))
        except Exception:
            return len(text.split())

    def encode(
        self,
        texts: Sequence[str],
        *,
        batch_size: int | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        max_tokens = int(getattr(model, "max_seq_length", 256) or 256)
        windows_by_text = [_windows_for_text(model, text, max_tokens) for text in texts]
        flat_windows = [window for windows in windows_by_text for window in windows]
        try:
            vectors = model.encode(
                flat_windows,
                batch_size=batch_size or self.batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise EmbeddingError("Could not generate embeddings") from exc

        encoded = cast(list[list[float]], vectors.tolist())
        if len(encoded) != len(flat_windows):
            raise EmbeddingError("The embedding model returned an unexpected number of vectors")

        result: list[list[float]] = []
        offset = 0
        for windows in windows_by_text:
            group = encoded[offset : offset + len(windows)]
            offset += len(windows)
            if len(group) == 1:
                vector = group[0]
                if len(vector) != self.dimension:
                    raise EmbeddingError(
                        f"Embedding vector has {len(vector)} dimensions, expected {self.dimension}"
                    )
                result.append(vector)
            else:
                result.append(_mean_normalize(group, self.dimension))
        return result


@lru_cache(maxsize=1)
def get_embedder() -> TextEmbedder:
    settings = get_settings()
    if settings.hf_home:
        # Hugging Face reads this environment variable when the model loads.
        os.environ.setdefault("HF_HOME", settings.hf_home)
    return SentenceTransformerEmbedder(
        model_name=settings.embedding_model,
        dimension=settings.embedding_dim,
        batch_size=settings.embedding_batch_size,
    )
