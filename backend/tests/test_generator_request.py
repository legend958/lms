"""Groq request-shape tests that do not touch the network."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from app.core.config import Settings
from app.rag.generator import GroqAnswerGenerator
from app.rag.retriever import RetrievedChunk


class _FakeCompletions:
    def __init__(self, captured: list[dict[str, Any]]) -> None:
        self.captured = captured

    async def create(self, **kwargs: Any) -> Any:
        self.captured.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Grounded answer [1]."))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
        )


class _FakeAsyncGroq:
    captured: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        del kwargs
        self.chat = SimpleNamespace(completions=_FakeCompletions(type(self).captured))

    async def close(self) -> None:
        return None


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        vector_id="doc:0",
        document_id=uuid.uuid4(),
        course_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        title="photosynthesis.pdf",
        text="Chloroplasts capture light in the thylakoid membranes.",
        page=1,
        section_title="Chloroplasts",
        similarity=0.8,
    )


async def test_answer_requests_low_reasoning_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAsyncGroq.captured = []
    monkeypatch.setattr("groq.AsyncGroq", _FakeAsyncGroq)
    settings = Settings(
        groq_api_key="test-key",
        groq_reasoning_effort="low",
    )

    generated = await GroqAnswerGenerator(settings).answer("How does it work?", [_chunk()])

    assert generated.refusal is False
    assert _FakeAsyncGroq.captured[0]["reasoning_effort"] == "low"


async def test_answer_omits_reasoning_effort_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeAsyncGroq.captured = []
    monkeypatch.setattr("groq.AsyncGroq", _FakeAsyncGroq)
    settings = Settings(
        groq_api_key="test-key",
        groq_reasoning_effort=None,
    )

    await GroqAnswerGenerator(settings).answer("How does it work?", [_chunk()])

    assert "reasoning_effort" not in _FakeAsyncGroq.captured[0]
