"""User queries."""

from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import Role, UserStatus
from app.db.models import User
from app.db.pagination import decode_cursor


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email.lower().strip()))
    return result.scalar_one_or_none()


async def get(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def list_users(
    session: AsyncSession,
    *,
    role: Role | None = None,
    search: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    include_deleted: bool = False,
) -> tuple[list[User], str | None]:
    stmt = select(User)
    if not include_deleted:
        stmt = stmt.where(User.status != UserStatus.DELETED)
    if role is not None:
        stmt = stmt.where(User.role == role)
    if search:
        pattern = f"%{search.strip().lower()}%"
        stmt = stmt.where(or_(User.name.ilike(pattern), User.email.ilike(pattern)))
    if cursor:
        created_at, entity_id = decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                User.created_at < created_at,
                and_(User.created_at == created_at, User.id < entity_id),
            )
        )
    stmt = stmt.order_by(User.created_at.desc(), User.id.desc()).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        from app.db.pagination import encode_cursor

        next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id)
    return rows, next_cursor


async def count_by_role(session: AsyncSession) -> dict[Role, int]:
    from sqlalchemy import func

    stmt = (
        select(User.role, func.count(User.id))
        .where(User.status != UserStatus.DELETED)
        .group_by(User.role)
    )
    rows = (await session.execute(stmt)).all()
    return {role: count for role, count in rows}
