"""Sliding-window rate limiting.

The in-process implementation is the fallback for tests and for development
where Redis is not configured. The production implementation keeps the same
exact sliding-window semantics in Redis so the budget is shared by every API
worker process instead of being multiplied by the worker count.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque

import redis.asyncio as redis
import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)

# KEYS[1] = bucket key
# ARGV[1] = now (ms), ARGV[2] = window (ms), ARGV[3] = limit, ARGV[4] = member
#
# Mirrors SlidingWindowLimiter.allow: drop entries at or before the cutoff,
# reject when the bucket is full, otherwise record this hit. Returns
# {allowed, retry_after_seconds} so callers behave identically in both modes.
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = window - (now - tonumber(oldest[2]))
  retry = math.ceil(retry / 1000)
  if retry < 1 then retry = 1 end
  return {0, retry}
end

redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window)
return {1, 0}
"""


class SlidingWindowLimiter:
    """In-process exact sliding window (fallback and tests)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        cutoff = now - window_seconds
        async with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
            return True, 0

    async def reset(self, key: str | None = None) -> None:
        async with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


class RedisSlidingWindowLimiter:
    """Exact sliding window shared across API workers via Redis.

    Redis is optional. If it is not configured or a call fails, the in-process
    limiter is used so authentication stays rate-limited rather than open.
    """

    def __init__(self) -> None:
        self._local = SlidingWindowLimiter()

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        settings = get_settings()
        if not settings.redis_url:
            return await self._local.allow(key, limit=limit, window_seconds=window_seconds)
        try:
            return await self._redis_allow(
                settings.redis_url, key, limit=limit, window_seconds=window_seconds
            )
        except Exception as exc:
            logger.warning("login_ratelimit_redis_failed", error=str(exc))
            return await self._local.allow(key, limit=limit, window_seconds=window_seconds)

    async def _redis_allow(
        self,
        url: str,
        key: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        now_ms = int(time.time() * 1000)
        window_ms = int(window_seconds * 1000)
        client = redis.from_url(
            url,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        try:
            result = await client.eval(  # type: ignore[no-untyped-call]
                _SLIDING_WINDOW_LUA,
                1,
                key,
                now_ms,
                window_ms,
                limit,
                f"{now_ms}:{uuid.uuid4().hex}",
            )
            allowed, retry_after = int(result[0]), int(result[1])
            return bool(allowed), retry_after
        finally:
            await client.aclose()  # type: ignore[attr-defined]

    async def reset(self, key: str | None = None) -> None:
        """Clear local state and, when configured, the matching Redis buckets."""
        await self._local.reset(key)
        settings = get_settings()
        if not settings.redis_url:
            return
        client = redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        try:
            if key is None:
                async for found in client.scan_iter(match="login:*", count=500):
                    await client.delete(found)
            else:
                await client.delete(key)
        except Exception as exc:  # pragma: no cover - Redis unavailable in tests
            logger.warning("login_ratelimit_reset_failed", error=str(exc))
        finally:
            await client.aclose()  # type: ignore[attr-defined]


login_limiter = RedisSlidingWindowLimiter()
