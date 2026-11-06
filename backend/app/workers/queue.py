"""Ingestion queue abstraction.

Production uses ARQ/Redis. A single-node development environment can run the
same pipeline in-process, and tests disable enqueueing entirely.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from arq.connections import RedisSettings, create_pool

from app.core.config import get_settings
from app.services.ingestion_service import reconcile_vectors, run_ingest

logger = structlog.get_logger(__name__)
_background_tasks: set[asyncio.Task[Any]] = set()


async def _run_inline(document_id: uuid.UUID) -> None:
    try:
        await run_ingest(document_id)
    except Exception:
        logger.exception("inline_ingest_failed", document_id=str(document_id))


def _schedule_inline(document_id: uuid.UUID) -> None:
    task = asyncio.create_task(_run_inline(document_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _run_quiz_inline(quiz_id: uuid.UUID) -> None:
    from app.services.quiz_service import run_quiz_generation

    try:
        await run_quiz_generation(quiz_id)
    except Exception:
        logger.exception("inline_quiz_generation_failed", quiz_id=str(quiz_id))


def _schedule_quiz_inline(quiz_id: uuid.UUID) -> None:
    task = asyncio.create_task(_run_quiz_inline(quiz_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _run_reconcile_inline() -> None:
    try:
        await reconcile_vectors()
    except Exception:
        logger.exception("inline_vector_reconcile_failed")


def _schedule_reconcile_inline() -> None:
    task = asyncio.create_task(_run_reconcile_inline())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def enqueue_ingest(document_id: uuid.UUID) -> bool:
    """Enqueue an ingestion job, returning whether it was accepted."""
    settings = get_settings()
    mode = settings.ingest_queue
    if mode == "disabled":
        logger.info("ingest_queue_disabled", document_id=str(document_id))
        return False

    if mode in {"auto", "arq"} and settings.redis_url:
        pool = None
        try:
            pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            await pool.enqueue_job(
                "ingest_document",
                str(document_id),
            )
            return True
        except Exception:
            logger.exception("ingest_enqueue_failed", document_id=str(document_id))
            if mode == "arq":
                return False
        finally:
            if pool is not None:
                await pool.aclose()  # type: ignore[attr-defined]

    if mode in {"auto", "inline"}:
        _schedule_inline(document_id)
        return True

    logger.warning(
        "ingest_queue_unconfigured",
        document_id=str(document_id),
        mode=mode,
        redis_configured=bool(settings.redis_url),
    )
    return False


async def enqueue_quiz_generation(quiz_id: uuid.UUID) -> bool:
    """Queue quiz generation, or run it in-process in development."""
    settings = get_settings()
    mode = settings.ingest_queue
    if mode == "disabled":
        logger.info("quiz_queue_disabled", quiz_id=str(quiz_id))
        return False

    if mode in {"auto", "arq"} and settings.redis_url:
        pool = None
        try:
            pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            await pool.enqueue_job("generate_quiz", str(quiz_id))
            return True
        except Exception:
            logger.exception("quiz_enqueue_failed", quiz_id=str(quiz_id))
            if mode == "arq":
                return False
        finally:
            if pool is not None:
                await pool.aclose()  # type: ignore[attr-defined]

    if mode in {"auto", "inline"}:
        _schedule_quiz_inline(quiz_id)
        return True

    logger.warning(
        "quiz_queue_unconfigured",
        quiz_id=str(quiz_id),
        mode=mode,
        redis_configured=bool(settings.redis_url),
    )
    return False


async def enqueue_reconcile() -> bool:
    """Queue a vector-metadata repair, or run one in-process in development."""
    settings = get_settings()
    mode = settings.ingest_queue
    if mode == "disabled":
        logger.info("reconcile_queue_disabled")
        return False

    if mode in {"auto", "arq"} and settings.redis_url:
        pool = None
        try:
            pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            await pool.enqueue_job("reconcile_vectors")
            return True
        except Exception:
            logger.exception("reconcile_enqueue_failed")
            if mode == "arq":
                return False
        finally:
            if pool is not None:
                await pool.aclose()  # type: ignore[attr-defined]

    if mode in {"auto", "inline"}:
        _schedule_reconcile_inline()
        return True
    return False
