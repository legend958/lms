"""Vector-store filters for role-scoped retrieval.

Student retrieval must always include both the enrolled course scope and the
denormalized ``released`` flag. Keeping this in one helper prevents a new query
from accidentally forgetting one of the two clauses.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any


def student_vector_where(
    course_ids: Iterable[uuid.UUID],
    *,
    topic_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    clauses: list[dict[str, Any]] = [
        {"course_id": {"$in": sorted({str(course_id) for course_id in course_ids})}},
        {"released": {"$eq": True}},
    ]
    if topic_id is not None:
        clauses.append({"topic_id": {"$eq": str(topic_id)}})
    return {"$and": clauses}
