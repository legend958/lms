from __future__ import annotations

import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "postgresql://user:pass@db:5432/lms",
            "postgresql+asyncpg://user:pass@db:5432/lms",
        ),
        (
            "postgres://user:pass@db:5432/lms",
            "postgresql+asyncpg://user:pass@db:5432/lms",
        ),
        (
            "postgresql+asyncpg://user:pass@db:5432/lms",
            "postgresql+asyncpg://user:pass@db:5432/lms",
        ),
    ],
)
def test_database_url_uses_the_async_driver(raw: str, expected: str) -> None:
    assert Settings(database_url=raw).database_url == expected


def test_database_url_rejects_an_empty_value() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(database_url="   ")
