"""ARQ enqueue contract.

``enqueue_job`` forwards any keyword it does not recognise to the job function,
so a stray ``_max_tries=3`` becomes a runtime ``TypeError`` inside the worker
rather than a retry policy. Retries come from ``WorkerSettings.max_tries`` and
the explicit ``Retry`` in ``app.workers.ingest``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.workers import queue


class FakePool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.closed = False

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append((function, args, kwargs))
        return SimpleNamespace(job_id="job-1")

    async def aclose(self) -> None:
        self.closed = True


def _patch_settings(monkeypatch: pytest.MonkeyPatch, pool: FakePool) -> None:
    monkeypatch.setattr(
        queue,
        "get_settings",
        lambda: SimpleNamespace(ingest_queue="arq", redis_url="redis://localhost:6379/0"),
    )

    async def fake_create_pool(_settings: Any) -> FakePool:
        return pool

    monkeypatch.setattr(queue, "create_pool", fake_create_pool)


async def test_enqueue_ingest_sends_only_the_document_id(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool()
    _patch_settings(monkeypatch, pool)
    document_id = uuid.uuid4()

    accepted = await queue.enqueue_ingest(document_id)

    assert accepted is True
    assert pool.calls == [("ingest_document", (str(document_id),), {})]
    assert pool.closed is True


async def test_enqueue_reconcile_sends_no_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool()
    _patch_settings(monkeypatch, pool)

    accepted = await queue.enqueue_reconcile()

    assert accepted is True
    assert pool.calls == [("reconcile_vectors", (), {})]
    assert pool.closed is True
