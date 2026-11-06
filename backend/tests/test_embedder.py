from __future__ import annotations

from typing import Any

from app.rag.embedder import SentenceTransformerEmbedder


class FakeTokenizer:
    def __call__(self, text: str, add_special_tokens: bool = False) -> dict[str, list[int]]:
        del add_special_tokens
        return {"input_ids": list(range(len(text.split())))}

    def decode(self, ids: list[int], skip_special_tokens: bool = True) -> str:
        del skip_special_tokens
        return f"window-{ids[0]}-{ids[-1]}"


class FakeVectors:
    def __init__(self, rows: list[list[float]]) -> None:
        self._rows = rows

    def tolist(self) -> list[list[float]]:
        return self._rows


class FakeModel:
    max_seq_length = 4

    def __init__(self) -> None:
        self.tokenizer = FakeTokenizer()
        self.seen: list[str] = []

    def encode(self, texts: list[str], **kwargs: Any) -> FakeVectors:
        del kwargs
        self.seen = list(texts)
        return FakeVectors([[1.0, 0.0] for _ in texts])


def test_long_chunks_are_embedded_as_overlapping_windows() -> None:
    embedder = SentenceTransformerEmbedder(model_name="fake", dimension=2, batch_size=8)
    model = FakeModel()
    embedder._model = model  # type: ignore[attr-defined]

    vectors = embedder.encode(["one two three four five six"])

    assert len(vectors) == 1
    assert vectors[0] == [1.0, 0.0]
    assert model.seen == ["window-0-3", "window-2-5"]


def test_short_chunks_use_a_single_window() -> None:
    embedder = SentenceTransformerEmbedder(model_name="fake", dimension=2, batch_size=8)
    model = FakeModel()
    embedder._model = model  # type: ignore[attr-defined]

    embedder.encode(["one two"])

    assert model.seen == ["one two"]
