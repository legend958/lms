"""Auth and current-user schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.api.schemas.common import ORMModel
from app.core.roles import CourseRole, Role, UserStatus


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserOut(ORMModel):
    id: uuid.UUID
    name: str
    email: str
    role: Role
    status: UserStatus
    created_at: datetime


class MembershipOut(BaseModel):
    id: uuid.UUID
    code: str
    title: str
    course_role: CourseRole


class MeOut(UserOut):
    courses: list[MembershipOut] = Field(default_factory=list)


class LoginResponse(BaseModel):
    user: UserOut
