"""align enum column widths with the model

Every enum column is declared ``length=20`` in ``app.db.models._enum``, but the
earlier migrations let SQLAlchemy infer the width from the longest enum member
at the time. ``ingestion_jobs.stage`` was therefore created as ``VARCHAR(10)``
(longest value then was ``extracting``), while Phase 3 writes
``topic_extraction`` (16 characters). SQLite ignores ``VARCHAR`` lengths, so the
test suite never noticed; Postgres raised ``StringDataRightTruncationError`` and
every ingestion failed at the topic-extraction stage.

Widening every enum column to ``VARCHAR(20)`` keeps the schema aligned with the
model and leaves headroom for future status values.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

WIDTH = 20

# (table, column) for every Enum(native_enum=False) column in the schema.
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
)


def _widen(table: str, column: str, width: int) -> None:
    # Batch mode reflects the table, so SQLite rebuilds it while Postgres issues
    # a plain ALTER COLUMN TYPE.
    with op.batch_alter_table(table) as batch:
        batch.alter_column(column, type_=sa.String(length=width))


def upgrade() -> None:
    for table, column in ENUM_COLUMNS:
        _widen(table, column, WIDTH)


def downgrade() -> None:
    # The original widths were an accident, so downgrade restores the inferred
    # longest-value widths only for the column that caused the outage. Widening
    # is lossless, so leaving the rest at 20 is safe.
    _widen("ingestion_jobs", "stage", 10)
