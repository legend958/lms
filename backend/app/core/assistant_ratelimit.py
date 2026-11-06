"""Per-student study-assistant rate limiting.

Production uses a Redis counter so the 20/hour budget is shared across API
replicas. When Redis is not configured (development, tests) or a Redis call
fails, the in-process sliding window keeps the endpoint usable instead of
failing closed.
"""

from __future__ import annotations

import time
from functools import lru_cache

import redis.asyncio as redis
import structlog

from app.core.config import Settings, get_settings
from app.core.ratelimit import SlidingWindowLimiter

logger = structlog.get_logger(__name__)

WINDOW_SECONDS = 3600


class AssistantRateLimiter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.limit = max(1, settings.assistant_queries_per_hour)
        self._local = SlidingWindowLimiter()

    async def allow(self, user_id: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``."""
        self.limit = max(1, self.settings.assistant_queries_per_hour)
        if not self.settings.redis_url:
            return await self._local_allow(user_id)
        try:
            return await self._redis_allow(user_id)
        except Exception as exc:
            logger.warning("assistant_ratelimit_redis_failed", error=str(exc))
            return await self._local_allow(user_id)

    async def _local_allow(self, user_id: str) -> tuple[bool, int]:
        return await self._local.allow(
            f"assistant:{user_id}",
            limit=self.limit,
            window_seconds=WINDOW_SECONDS,
        )

    async def _redis_allow(self, user_id: str) -> tuple[bool, int]:
        now = int(time.time())
        bucket = now // WINDOW_SECONDS
        key = f"assistant:rl:{user_id}:{bucket}"
        client = redis.from_url(
            self.settings.redis_url or "",
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        try:
            count = int(await client.incr(key))
            if count == 1:
                await client.expire(key, WINDOW_SECONDS)
            if count > self.limit:
                retry_after = WINDOW_SECONDS - (now % WINDOW_SECONDS)
                return False, max(1, retry_after)
            return True, 0
        finally:
            await client.aclose()  # type: ignore[attr-defined]

    async def reset(self, user_id: str | None = None) -> None:
        await self._local.reset(None if user_id is None else f"assistant:{user_id}")


@lru_cache(maxsize=1)
def get_assistant_limiter() -> AssistantRateLimiter:
    return AssistantRateLimiter(get_settings())
