"""Admin user-management schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.api.schemas.auth import UserOut
from app.core.roles import Role, UserStatus


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    role: Role = Role.STUDENT
    course_ids: list[uuid.UUID] = []
    password: str | None = Field(default=None, min_length=8, max_length=256)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Role | None = None
    status: UserStatus | None = None


class UserAdminOut(UserOut):
    created_at: datetime


class UserCreated(UserAdminOut):
    temporary_password: str | None = None
