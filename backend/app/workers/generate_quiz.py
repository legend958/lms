"""ARQ quiz generation job."""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from app.services.quiz_service import run_quiz_generation

logger = structlog.get_logger(__name__)


async def generate_quiz(ctx: dict[str, Any], quiz_id: str) -> str:
    """Generate questions for one draft quiz.

    Retries live inside the Groq generator, and every failure is recorded on the
    quiz row (``generation_status='failed'``), so the job itself does not retry
    and risk duplicate work.
    """
    try:
        parsed = uuid.UUID(quiz_id)
    except ValueError:
        logger.error("quiz_generation_invalid_id", quiz_id=quiz_id)
        return quiz_id

    logger.info("quiz_generation_started", quiz_id=quiz_id, attempt=ctx.get("job_try", 1))
    await run_quiz_generation(parsed)
    return quiz_id
