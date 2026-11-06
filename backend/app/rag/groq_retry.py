"""Backoff for Groq calls.

Groq enforces a tokens-per-minute budget and answers an over-budget request with
``429`` plus a ``Retry-After`` header (and a "try again in N s" message). A
fixed sub-second backoff retries inside the same window and burns the remaining
attempt, which is what made quiz generation fail right after topic extraction.
"""

from __future__ import annotations

import re

_RETRY_IN = re.compile(r"try again in ([0-9.]+)\s*s", re.IGNORECASE)
_MAX_DELAY_SECONDS = 30.0


def retry_delay(exc: BaseException, attempt: int) -> float:
    """Return how long to wait before retrying ``exc``.

    ``attempt`` is 1-based. The provider's own hint is preferred; when it is not
    available, fall back to exponential backoff (2 s, 4 s, ...). The result is
    capped so a permanently failing call cannot hold a worker for minutes.
    """
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is not None:
        raw = headers.get("retry-after")
        if raw:
            try:
                return _from_hint(raw)
            except ValueError:
                pass

    match = _RETRY_IN.search(str(exc))
    if match:
        try:
            return _from_hint(match.group(1))
        except ValueError:
            pass

    return min(_MAX_DELAY_SECONDS, float(2**attempt))


def _from_hint(raw: str) -> float:
    # A one-second cushion avoids retrying on the exact window boundary, where
    # a request can still be counted against the previous minute.
    return min(_MAX_DELAY_SECONDS, max(1.0, float(raw)) + 1.0)
