"""Admin user management endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, request_meta, require_role
from app.api.schemas.common import Page
from app.api.schemas.users import UserAdminOut, UserCreate, UserCreated, UserUpdate
from app.core.roles import Role
from app.db.models import User
from app.db.repositories import users as users_repo
from app.db.session import get_session
from app.services import user_service

router = APIRouter(
    prefix="/admin/users",
    tags=["admin:users"],
    dependencies=[Depends(require_role(Role.ADMIN))],
)


@router.get("", response_model=Page[UserAdminOut])
async def list_users(
    role: Role | None = None,
    search: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> Page[UserAdminOut]:
    rows, next_cursor = await users_repo.list_users(
        session, role=role, search=search, cursor=cursor, limit=limit
    )
    return Page(
        items=[UserAdminOut.model_validate(row) for row in rows],
        next_cursor=next_cursor,
    )


@router.post("", response_model=UserCreated, status_code=201)
async def create_user(
    payload: UserCreate,
    request: Request,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserCreated:
    ip, request_id = request_meta(request)
    user, temporary_password = await user_service.create_user(
        session,
        actor=actor,
        name=payload.name,
        email=str(payload.email),
        role=payload.role,
        course_ids=payload.course_ids,
        password=payload.password,
        ip=ip,
        request_id=request_id,
    )
    data = UserAdminOut.model_validate(user)
    return UserCreated(**data.model_dump(), temporary_password=temporary_password)


@router.patch("/{user_id}", response_model=UserAdminOut)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserAdminOut:
    ip, request_id = request_meta(request)
    user = await user_service.update_user(
        session,
        actor=actor,
        user_id=user_id,
        changes=payload.model_dump(exclude_unset=True),
        ip=ip,
        request_id=request_id,
    )
    return UserAdminOut.model_validate(user)


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    ip, request_id = request_meta(request)
    await user_service.delete_user(
        session, actor=actor, user_id=user_id, ip=ip, request_id=request_id
    )
