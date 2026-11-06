"""task progress and notifications

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

task_progress_status = sa.Enum(
    "completed",
    "incomplete",
    name="task_progress_status",
    native_enum=False,
)
notification_type = sa.Enum(
    "task_released",
    name="notification_type",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "task_progress",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("status", task_progress_status, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id", "student_id"),
    )
    op.create_index(
        "ix_task_progress_student_status",
        "task_progress",
        ["student_id", "status"],
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", notification_type, nullable=False),
        sa.Column("payload", JSON_TYPE, nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index(
        "ix_notifications_user_created",
        "notifications",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_notifications_user_read",
        "notifications",
        ["user_id", "read_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_user_read", table_name="notifications")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("ix_task_progress_student_status", table_name="task_progress")
    op.drop_table("task_progress")
