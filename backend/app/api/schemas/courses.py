"""Course and enrollment schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.api.schemas.common import ORMModel
from app.core.roles import CourseRole, CourseStatus


class CourseCreate(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    term: str | None = Field(default=None, max_length=60)
    description: str | None = None


class CourseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    term: str | None = Field(default=None, max_length=60)
    description: str | None = None
    status: CourseStatus | None = None


class CourseOut(ORMModel):
    id: uuid.UUID
    code: str
    title: str
    term: str | None
    description: str | None
    status: CourseStatus
    created_by: uuid.UUID
    created_at: datetime


class EnrollmentCreate(BaseModel):
    user_ids: list[uuid.UUID] = Field(min_length=1)
    course_role: CourseRole = CourseRole.STUDENT


class EnrollmentOut(BaseModel):
    user_id: uuid.UUID
    name: str
    email: str
    course_role: CourseRole


class EnrollmentResult(BaseModel):
    added: int
