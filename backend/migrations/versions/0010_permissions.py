"""permissions table and capability seeds

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-03
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

permission_role = sa.Enum(
    "student",
    "teacher",
    "admin",
    name="permission_role",
    native_enum=False,
)

CAPABILITIES = [
    ("student.self_enroll", "student", False),
    ("teacher.create_course", "teacher", False),
    ("student.assistant_enabled", "student", True),
    ("quiz.auto_approve", "teacher", True),
]


def upgrade() -> None:
    op.create_table(
        "permissions",
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("role", permission_role, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", "role"),
    )

    now = datetime.now(UTC)
    permission_table = sa.table(
        "permissions",
        sa.column("key", sa.String),
        sa.column("role", permission_role),
        sa.column("enabled", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        permission_table,
        [
            {
                "key": key,
                "role": role,
                "enabled": enabled,
                "created_at": now,
                "updated_at": now,
            }
            for key, role, enabled in CAPABILITIES
        ],
    )


def downgrade() -> None:
    op.drop_table("permissions")
