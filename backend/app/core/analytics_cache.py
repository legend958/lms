"""Short-lived cache for teacher analytics responses.

Redis is used when configured so every API replica shares the same 60-second
window. Without Redis (development and tests) an in-process dictionary with the
same TTL keeps the endpoint fast. Analytics data is small and read-heavy, so a
cache miss is always safe: the service recomputes from Postgres.

Keys are ``analytics:{course_id}:{filter_hash}``. Invalidation removes every
filter variant for a course, which is what release, completion, and submission
events need.
"""

from __future__ import annotations

import hashlib
import json
import time
from functools import lru_cache
from typing import Any

import redis.asyncio as redis
import structlog

from app.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)

CACHE_TTL_SECONDS = 60
KEY_PREFIX = "analytics"


class AnalyticsCache:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._local: dict[str, tuple[float, str]] = {}

    @staticmethod
    def filter_hash(payload: Any) -> str:
        """Stable short digest for a set of query parameters."""
        canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _key(course_id: str, filter_hash: str) -> str:
        return f"{KEY_PREFIX}:{course_id}:{filter_hash}"

    async def get(self, course_id: str, filter_hash: str) -> Any | None:
        key = self._key(course_id, filter_hash)
        if not self.settings.redis_url:
            return self._local_get(key)
        try:
            return await self._redis_get(key)
        except Exception as exc:
            logger.warning("analytics_cache_redis_get_failed", error=str(exc))
            return self._local_get(key)

    async def set(self, course_id: str, filter_hash: str, value: Any) -> None:
        key = self._key(course_id, filter_hash)
        encoded = json.dumps(value, default=str, separators=(",", ":"))
        if not self.settings.redis_url:
            self._local_set(key, encoded)
            return
        try:
            await self._redis_set(key, encoded)
        except Exception as exc:
            logger.warning("analytics_cache_redis_set_failed", error=str(exc))
            self._local_set(key, encoded)

    async def invalidate(self, course_id: str) -> None:
        """Drop every cached filter variant for one course."""
        prefix = f"{KEY_PREFIX}:{course_id}:"
        for key in [key for key in self._local if key.startswith(prefix)]:
            self._local.pop(key, None)
        if not self.settings.redis_url:
            return
        try:
            await self._redis_invalidate(prefix)
        except Exception as exc:
            logger.warning("analytics_cache_redis_invalidate_failed", error=str(exc))

    def reset(self) -> None:
        """Clear the in-process cache (used by tests)."""
        self._local.clear()

    def _local_get(self, key: str) -> Any | None:
        entry = self._local.get(key)
        if entry is None:
            return None
        expires_at, encoded = entry
        if expires_at <= time.monotonic():
            self._local.pop(key, None)
            return None
        return json.loads(encoded)

    def _local_set(self, key: str, encoded: str) -> None:
        self._local[key] = (time.monotonic() + CACHE_TTL_SECONDS, encoded)

    async def _redis_get(self, key: str) -> Any | None:
        client = self._client()
        try:
            raw = await client.get(key)
        finally:
            await client.aclose()  # type: ignore[attr-defined]
        if raw is None:
            return None
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        return json.loads(text)

    async def _redis_set(self, key: str, encoded: str) -> None:
        client = self._client()
        try:
            await client.set(key, encoded, ex=CACHE_TTL_SECONDS)
        finally:
            await client.aclose()  # type: ignore[attr-defined]

    async def _redis_invalidate(self, prefix: str) -> None:
        client = self._client()
        try:
            batch: list[bytes] = []
            async for key in client.scan_iter(match=f"{prefix}*", count=100):
                batch.append(key)
                if len(batch) >= 100:
                    await client.delete(*batch)
                    batch.clear()
            if batch:
                await client.delete(*batch)
        finally:
            await client.aclose()  # type: ignore[attr-defined]

    def _client(self) -> redis.Redis[bytes]:
        return redis.from_url(
            self.settings.redis_url or "",
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=False,
        )


@lru_cache(maxsize=1)
def get_analytics_cache() -> AnalyticsCache:
    return AnalyticsCache(get_settings())
