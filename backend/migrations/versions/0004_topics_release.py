"""topics, tasks, and the release gate

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

topic_release_state = sa.Enum(
    "draft",
    "released",
    "scheduled",
    name="topic_release_state",
    native_enum=False,
)
topic_source = sa.Enum("llm", "fallback", name="topic_source", native_enum=False)
task_status = sa.Enum(
    "draft",
    "published",
    "archived",
    name="task_status",
    native_enum=False,
)


def upgrade() -> None:
    op.add_column("documents", sa.Column("topics_source", topic_source, nullable=True))

    op.create_table(
        "topics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("release_state", topic_release_state, nullable=False),
        sa.Column("released_by", sa.Uuid(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "needs_review",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["released_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_topics_document_id", "topics", ["document_id"])
    op.create_index("ix_topics_course_id", "topics", ["course_id"])
    op.create_index(
        "ix_topics_document_release",
        "topics",
        ["document_id", "release_state"],
    )
    op.create_index(
        "ix_topics_course_release",
        "topics",
        ["course_id", "release_state"],
    )
    op.create_index("ix_topics_document_order", "topics", ["document_id", "order_index"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("week_no", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", task_status, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id", name="uq_tasks_topic"),
    )
    op.create_index("ix_tasks_course_id", "tasks", ["course_id"])
    op.create_index("ix_tasks_course_due", "tasks", ["course_id", "due_at"])

    with op.batch_alter_table("chunks") as batch_op:
        batch_op.create_foreign_key(
            "fk_chunks_topic_id_topics",
            "topics",
            ["topic_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_chunks_topic_released", "chunks", ["topic_id", "released"])


def downgrade() -> None:
    op.drop_index("ix_chunks_topic_released", table_name="chunks")
    with op.batch_alter_table("chunks") as batch_op:
        batch_op.drop_constraint("fk_chunks_topic_id_topics", type_="foreignkey")

    op.drop_index("ix_tasks_course_due", table_name="tasks")
    op.drop_index("ix_tasks_course_id", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ix_topics_document_order", table_name="topics")
    op.drop_index("ix_topics_course_release", table_name="topics")
    op.drop_index("ix_topics_document_release", table_name="topics")
    op.drop_index("ix_topics_course_id", table_name="topics")
    op.drop_index("ix_topics_document_id", table_name="topics")
    op.drop_table("topics")

    op.drop_column("documents", "topics_source")
