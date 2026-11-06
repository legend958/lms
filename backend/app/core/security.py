"""Password hashing, JWT access tokens, refresh tokens, and CSRF tokens."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings
from app.core.roles import Role

ALGORITHM = "HS256"
ACCESS_COOKIE = "lms_access"
REFRESH_COOKIE = "lms_refresh"
CSRF_COOKIE = "lms_csrf"
ROLE_COOKIE = "lms_role"

# Tuned for interactive logins. Memory cost is deliberately moderate so test
# suites and small containers stay responsive.
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=2)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> tuple[bool, bool]:
    """Return (is_valid, needs_rehash)."""
    try:
        _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False, False
    return True, _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    role: Role
    jti: str
    expires_at: datetime


def create_access_token(user_id: uuid.UUID, role: Role, settings: Settings) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.access_token_ttl_min)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role.value,
        "type": "access",
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.session_secret, algorithm=ALGORITHM)
    return token, expires_at


def decode_access_token(token: str, settings: Settings) -> AccessTokenClaims:
    payload = jwt.decode(token, settings.session_secret, algorithms=[ALGORITHM])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("unexpected token type")
    return AccessTokenClaims(
        user_id=uuid.UUID(str(payload["sub"])),
        role=Role(str(payload["role"])),
        jti=str(payload["jti"]),
        expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
    )


def generate_refresh_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hash). Only the hash is persisted."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def constant_time_equals(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
