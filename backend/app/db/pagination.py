"""Cursor pagination helpers (created_at, id ordering)."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Any

from app.core.errors import ValidationError


def encode_cursor(created_at: datetime, entity_id: uuid.UUID) -> str:
    payload = json.dumps({"t": created_at.isoformat(), "i": str(entity_id)}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(payload["t"]), uuid.UUID(payload["i"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValidationError("Invalid pagination cursor") from exc
