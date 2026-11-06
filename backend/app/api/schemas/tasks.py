"""Student task and dashboard DTOs.

These are deliberately separate from the teacher ``Task``/``Topic`` shapes and
never serialize release state, audit fields, or vector metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.api.schemas.announcements import AnnouncementOut
from app.core.roles import TaskProgressStatus

TaskFilter = Literal["all", "incomplete", "completed"]
TaskSort = Literal["due_at", "released_at"]


class StudentCourseRef(BaseModel):
    id: uuid.UUID
    code: str
    title: str


class StudentTaskOut(BaseModel):
    id: uuid.UUID
    topic_id: uuid.UUID
    title: str
    description: str
    course: StudentCourseRef
    week_no: int
    due_at: datetime
    status: TaskProgressStatus
    overdue: bool
    completed_at: datetime | None


class StudentTaskList(BaseModel):
    items: list[StudentTaskOut]
    next_cursor: str | None = None


class StudentTaskUpdate(BaseModel):
    status: TaskProgressStatus


class StudentTaskUpdateResult(BaseModel):
    status: TaskProgressStatus
    completed_at: datetime | None


class StudentTaskCounts(BaseModel):
    pending: int
    completed: int
    overdue: int


class StudentDashboardOut(BaseModel):
    counts: StudentTaskCounts
    due_soon: list[StudentTaskOut]
    announcements: list[AnnouncementOut]
