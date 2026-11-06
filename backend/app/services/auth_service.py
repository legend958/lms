"""Authentication: credential checks and refresh-token rotation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.roles import UserStatus
from app.core.security import (
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.db.models import RefreshToken, User
from app.db.repositories import users as users_repo


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    user = await users_repo.get_by_email(session, email)
    if user is None:
        return None
    valid, needs_rehash = verify_password(user.password_hash, password)
    if not valid:
        return None
    if needs_rehash:
        user.password_hash = hash_password(password)
    return user


def ensure_active(user: User) -> None:
    if user.status is not UserStatus.ACTIVE:
        raise UnauthorizedError("Account is not active")


async def issue_refresh_token(session: AsyncSession, user_id: uuid.UUID, settings: Settings) -> str:
    raw, token_hash = generate_refresh_token()
    row = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
    )
    session.add(row)
    await session.commit()
    return raw


async def rotate_refresh_token(
    session: AsyncSession, raw_token: str, settings: Settings
) -> tuple[User, str]:
    token_hash = hash_token(raw_token)
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    row = (await session.execute(stmt)).scalar_one_or_none()
    now = datetime.now(UTC)

    if row is None or row.revoked_at is not None:
        raise UnauthorizedError("Invalid refresh token")
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise UnauthorizedError("Refresh token expired")

    user = await users_repo.get(session, row.user_id)
    if user is None:
        raise UnauthorizedError("Invalid refresh token")
    ensure_active(user)

    new_raw, new_hash = generate_refresh_token()
    new_row = RefreshToken(
        user_id=user.id,
        token_hash=new_hash,
        expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
    )
    session.add(new_row)
    await session.flush()
    row.revoked_at = now
    row.replaced_by = new_row.id
    await session.commit()
    return user, new_raw


async def revoke_refresh_token(session: AsyncSession, raw_token: str) -> None:
    token_hash = hash_token(raw_token)
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.commit()
