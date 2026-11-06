"""Async engine and session lifecycle.

The engine is created lazily so tests can point DATABASE_URL at SQLite before
the first request. Production uses Postgres (asyncpg).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _build_engine(url: str) -> AsyncEngine:
    if url.startswith("sqlite"):
        kwargs: dict[str, object] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        return create_async_engine(url, **kwargs)
    return create_async_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)


def init_engine(url: str | None = None) -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine
    _engine = _build_engine(url or get_settings().database_url)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_engine() -> AsyncEngine:
    return _engine or init_engine()


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
