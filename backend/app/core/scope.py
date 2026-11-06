"""Course scoping.

Every student-facing query takes a CourseScope instead of a raw course id, so
forgetting a filter becomes a type error rather than a silent data leak.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.errors import NotFoundError
from app.core.roles import Role


@dataclass(frozen=True, slots=True)
class CourseScope:
    user_id: uuid.UUID
    role: Role
    course_ids: frozenset[uuid.UUID]

    def includes(self, course_id: uuid.UUID) -> bool:
        return course_id in self.course_ids

    def require(self, course_id: uuid.UUID) -> None:
        """Raise 404 (not 403) to avoid leaking resource existence."""
        if not self.includes(course_id):
            raise NotFoundError("Course not found")

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN
