"""ARQ worker settings."""

from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.workers.generate_quiz import generate_quiz
from app.workers.ingest import ingest_document, reconcile_vectors
from app.workers.scheduled import CRON_JOBS


async def ping(ctx: dict[str, Any]) -> str:  # pragma: no cover - smoke helper
    del ctx
    return "pong"


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        ping,
        ingest_document,
        reconcile_vectors,
        generate_quiz,
    ]
    cron_jobs: ClassVar[list[Any]] = CRON_JOBS
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379/0")
    max_jobs = 2
    job_timeout = 900
    max_tries = 3
    keep_result = 3600
