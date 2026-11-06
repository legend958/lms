"""Schema-level regressions that SQLite-backed API tests cannot catch.

SQLite ignores ``VARCHAR`` lengths, so a column that Postgres created as
``VARCHAR(10)`` happily stores ``topic_extraction`` in tests and then fails in
production with ``StringDataRightTruncationError``. These tests run the real
migration chain against a throwaway SQLite file and assert the declared widths
match the model.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Every Enum(native_enum=False) column. app.db.models._enum declares length=20.
ENUM_COLUMNS: tuple[tuple[str, str], ...] = (
    ("users", "role"),
    ("users", "status"),
    ("courses", "status"),
    ("enrollments", "course_role"),
    ("documents", "status"),
    ("documents", "topics_source"),
    ("ingestion_jobs", "stage"),
    ("topics", "release_state"),
    ("tasks", "status"),
    ("task_progress", "status"),
    ("notifications", "type"),
    ("quizzes", "status"),
    ("quizzes", "generation_status"),
    ("questions", "difficulty"),
)
MIN_ENUM_WIDTH = 20


def _alembic(database_url: str, *args: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stderr}"


def _declared_width(conn: sqlite3.Connection, table: str, column: str) -> int:
    for row in conn.execute(f"PRAGMA table_info({table})"):
        if row[1] == column:
            match = re.search(r"\((\d+)\)", row[2] or "")
            assert match is not None, f"{table}.{column} has no declared width: {row[2]}"
            return int(match.group(1))
    raise AssertionError(f"{table}.{column} is missing from the migrated schema")


def test_enum_columns_are_wide_enough(tmp_path: Path) -> None:
    database = tmp_path / "migrations.db"
    database_url = f"sqlite+aiosqlite:///{database.as_posix()}"

    _alembic(database_url, "upgrade", "head")

    conn = sqlite3.connect(database)
    try:
        widths = {
            (table, column): _declared_width(conn, table, column) for table, column in ENUM_COLUMNS
        }
    finally:
        conn.close()

    too_narrow = {key: width for key, width in widths.items() if width < MIN_ENUM_WIDTH}
    assert not too_narrow, f"enum columns narrower than {MIN_ENUM_WIDTH}: {too_narrow}"


def test_enum_width_migration_round_trips(tmp_path: Path) -> None:
    database = tmp_path / "migrations_round_trip.db"
    database_url = f"sqlite+aiosqlite:///{database.as_posix()}"

    _alembic(database_url, "upgrade", "head")
    _alembic(database_url, "downgrade", "-1")
    _alembic(database_url, "upgrade", "head")

    conn = sqlite3.connect(database)
    try:
        assert _declared_width(conn, "ingestion_jobs", "stage") >= MIN_ENUM_WIDTH
    finally:
        conn.close()


@pytest.mark.parametrize(("table", "column"), ENUM_COLUMNS)
def test_enum_column_accepts_its_longest_value(table: str, column: str) -> None:
    """Guard against a future enum member that outgrows the column."""
    from app.db import models  # noqa: F401  (populates metadata)
    from app.db.base import Base

    table_meta = Base.metadata.tables[table]
    enum_type = table_meta.columns[column].type
    longest = max(len(str(value)) for value in enum_type.enums)  # type: ignore[attr-defined]

    assert longest <= MIN_ENUM_WIDTH, (
        f"{table}.{column} member {longest} chars exceeds VARCHAR({MIN_ENUM_WIDTH}); "
        "widen the column before adding the member"
    )
