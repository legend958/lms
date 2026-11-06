"""Append-only audit trail."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


async def record(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
    request_id: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        before=before,
        after=after,
        ip=ip,
        request_id=request_id,
    )
    session.add(entry)
    return entry
