"""ARQ ingestion jobs."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from arq import Retry

from app.core.roles import IngestErrorCode
from app.services.ingestion_service import (
    DocumentMissingError,
    IngestError,
    reset_for_retry,
    run_ingest,
)
from app.services.ingestion_service import reconcile_vectors as reconcile_vectors_service

logger = structlog.get_logger(__name__)

RETRY_DELAYS = (30, 120, 300)
NON_RETRYABLE_CODES = frozenset({IngestErrorCode.ENCRYPTED_PDF, IngestErrorCode.NO_TEXT_LAYER})


def _should_retry(exc: Exception, attempt: int, max_tries: int) -> bool:
    if attempt >= max_tries:
        return False
    if isinstance(exc, DocumentMissingError):
        return False
    return not (isinstance(exc, IngestError) and exc.code in NON_RETRYABLE_CODES)


async def ingest_document(ctx: dict[str, Any], document_id: str) -> str:
    attempt = int(ctx.get("job_try", 1))
    max_tries = int(ctx.get("max_tries", 3))
    try:
        parsed = uuid.UUID(document_id)
    except ValueError:
        logger.error("ingest_invalid_document_id", document_id=document_id)
        return document_id

    try:
        await run_ingest(parsed, attempt=attempt)
    except Exception as exc:
        if _should_retry(exc, attempt, max_tries):
            await reset_for_retry(parsed)
            delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
            logger.warning(
                "ingest_retry_scheduled",
                document_id=document_id,
                attempt=attempt,
                delay_seconds=delay,
            )
            raise Retry(defer=delay) from exc
        logger.error(
            "ingest_failed",
            document_id=document_id,
            attempt=attempt,
            error_code=getattr(exc, "code", None),
        )
        raise

    return document_id


async def reconcile_vectors(ctx: dict[str, Any]) -> dict[str, int]:
    del ctx
    checked, repaired = await reconcile_vectors_service()
    return {"checked": checked, "repaired": repaired}
