"""Backoff selection for Groq rate-limited calls."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.rag.groq_retry import retry_delay


def _rate_limited(message: str, retry_after: str | None) -> Exception:
    headers = {} if retry_after is None else {"retry-after": retry_after}
    response = SimpleNamespace(headers=headers)
    exc = RuntimeError(message)
    exc.response = response  # type: ignore[attr-defined]
    return exc


def test_retry_after_header_is_honoured_with_a_cushion() -> None:
    exc = _rate_limited("rate limited", "16")
    assert retry_delay(exc, attempt=1) == 17.0


def test_message_hint_is_used_without_a_header() -> None:
    exc = _rate_limited("Please try again in 25.92s.", None)
    assert retry_delay(exc, attempt=1) == pytest.approx(26.92)


def test_hint_is_capped() -> None:
    exc = _rate_limited("rate limited", "600")
    assert retry_delay(exc, attempt=1) == 30.0


def test_exponential_fallback_when_no_hint_is_present() -> None:
    assert retry_delay(RuntimeError("connection reset"), attempt=1) == 2.0
    assert retry_delay(RuntimeError("connection reset"), attempt=3) == 8.0
