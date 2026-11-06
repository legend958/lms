"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import gc
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any, cast

import redis.asyncio as redis
import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import register_exception_handlers
from app.api.middleware import CSRFMiddleware, RequestContextMiddleware
from app.api.routers import (
    admin,
    announcements,
    auth,
    courses,
    documents,
    notifications,
    student_assistant,
    student_quiz,
    student_tasks,
    teacher_analytics,
    teacher_quiz,
    topics,
    users,
)
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import dispose_engine, get_session, init_engine
from app.rag.embedder import get_embedder
from app.rag.vector_store import VectorStore, get_vector_store

settings = get_settings()
configure_logging()
logger = structlog.get_logger(__name__)


def _lock_warmup() -> object | None:
    """Serialize model warmup across worker processes.

    Each worker warms the embedder in its own process. Loading torch and the
    weights in several workers at the same instant doubles the transient memory
    spike and gets workers OOM-killed on a small host. The API container's
    /tmp tmpfs is shared by its workers, so an advisory file lock lets them take
    turns. Returns the open handle (closing it releases the lock).
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows test hosts never warm
        return None
    module = cast(Any, fcntl)
    try:
        handle = open("/tmp/lms-embedder-warm.lock", "w")  # noqa: SIM115 - held until warmup ends
    except OSError:
        return None
    module.flock(handle.fileno(), module.LOCK_EX)
    return handle


async def _warm_assistant_embedder() -> None:
    """Load the embedding model before the first student question.

    The model takes tens of seconds to load on a cold process. Warming it in the
    background keeps startup and health checks fast while removing that cost
    from the first assistant request.

    The model graph is also the reason this process has a very large set of
    long-lived Python objects. A full-heap garbage collection has to scan all of
    them and can stall request handling for seconds, so the post-warmup heap is
    frozen once the long-lived objects exist. Only objects allocated after this
    point are collected.
    """
    lock = None
    try:
        lock = await asyncio.to_thread(_lock_warmup)
        embedder = get_embedder()
        await asyncio.to_thread(embedder.encode, ["study assistant warmup"])
        logger.info("assistant_embedder_warmed")
    except Exception as exc:  # pragma: no cover - depends on local model cache
        logger.warning("assistant_embedder_warmup_failed", error=str(exc))
    finally:
        if lock is not None:
            lock.close()  # type: ignore[attr-defined]
        gc.collect()
        gc.freeze()
        logger.info("gc_heap_frozen", frozen_objects=gc.get_freeze_count())


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_engine()
    warm_task: asyncio.Task[None] | None = None
    if settings.app_env != "test":
        warm_task = asyncio.create_task(_warm_assistant_embedder())
    try:
        yield
    finally:
        if warm_task is not None:
            warm_task.cancel()
            with suppress(asyncio.CancelledError):
                await warm_task
        await dispose_engine()


app = FastAPI(
    title="LMS API",
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

register_exception_handlers(app)

# Middleware is applied in reverse order: CORS outermost, then request context,
# then CSRF, so the request id exists before any rejection is logged.
app.add_middleware(CSRFMiddleware, settings=settings)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-request-id"],
)

API_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(users.router, prefix=API_PREFIX)
app.include_router(admin.router, prefix=API_PREFIX)
app.include_router(courses.router, prefix=API_PREFIX)
app.include_router(announcements.router, prefix=API_PREFIX)
app.include_router(documents.router, prefix=API_PREFIX)
app.include_router(topics.router, prefix=API_PREFIX)
app.include_router(topics.student_router, prefix=API_PREFIX)
app.include_router(teacher_quiz.router, prefix=API_PREFIX)
app.include_router(teacher_analytics.router, prefix=API_PREFIX)
app.include_router(student_quiz.router, prefix=API_PREFIX)
app.include_router(student_assistant.router, prefix=API_PREFIX)
app.include_router(student_tasks.router, prefix=API_PREFIX)
app.include_router(notifications.router, prefix=API_PREFIX)


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": settings.app_version}


@app.get("/readyz", tags=["system"])
async def readyz(
    session: AsyncSession = Depends(get_session),
    vector_store: VectorStore = Depends(get_vector_store),
) -> JSONResponse:
    checks: dict[str, str] = {}
    healthy = True

    try:
        await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "fail"
        healthy = False

    if settings.redis_url:
        client = redis.from_url(settings.redis_url, socket_connect_timeout=1)
        try:
            await client.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "fail"
            healthy = False
        finally:
            await client.aclose()  # type: ignore[attr-defined]
    else:
        checks["redis"] = "disabled"

    try:
        await asyncio.wait_for(asyncio.to_thread(vector_store.count), timeout=3)
        checks["chroma"] = "ok"
    except Exception:
        checks["chroma"] = "fail"
        healthy = False

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )
