"""Scheduled maintenance jobs."""

from __future__ import annotations

from typing import Any

from arq import cron

from app.services.ingestion_service import reconcile_vectors


async def hourly_reconcile(ctx: dict[str, Any]) -> dict[str, int]:
    del ctx
    checked, repaired = await reconcile_vectors()
    return {"checked": checked, "repaired": repaired}


# Minute zero of every hour.
CRON_JOBS = [cron(hourly_reconcile, minute={0})]
