"""rag_query_logs

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "rag_query_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=True),
        sa.Column("question_hash", sa.String(length=64), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=True),
        sa.Column("chunk_ids", JSON_TYPE, nullable=False),
        sa.Column("max_similarity", sa.Float(), nullable=False),
        sa.Column("refusal", sa.Boolean(), nullable=False),
        sa.Column("injection_flag", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rag_query_logs_user_created", "rag_query_logs", ["user_id", "created_at"])
    op.create_index(
        "ix_rag_query_logs_course_created", "rag_query_logs", ["course_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_rag_query_logs_course_created", table_name="rag_query_logs")
    op.drop_index("ix_rag_query_logs_user_created", table_name="rag_query_logs")
    op.drop_table("rag_query_logs")
