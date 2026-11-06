"""quizzes, questions, attempts, and answers

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
ENUM_WIDTH = 20

quiz_status = sa.Enum(
    "draft",
    "published",
    "archived",
    name="quiz_status",
    native_enum=False,
    length=ENUM_WIDTH,
)
quiz_generation_status = sa.Enum(
    "queued",
    "generating",
    "ready",
    "failed",
    name="quiz_generation_status",
    native_enum=False,
    length=ENUM_WIDTH,
)
question_difficulty = sa.Enum(
    "easy",
    "medium",
    "hard",
    name="question_difficulty",
    native_enum=False,
    length=ENUM_WIDTH,
)


def upgrade() -> None:
    op.create_table(
        "quizzes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("pass_threshold", sa.Integer(), nullable=False),
        sa.Column("attempt_limit", sa.Integer(), nullable=False),
        sa.Column("status", quiz_status, nullable=False),
        sa.Column("auto_approve", sa.Boolean(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("generation_status", quiz_generation_status, nullable=False),
        sa.Column("generation_error", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("topic_id", name="uq_quizzes_topic"),
    )
    op.create_index("ix_quizzes_course_id", "quizzes", ["course_id"])
    op.create_index("ix_quizzes_course_status", "quizzes", ["course_id", "status"])

    op.create_table(
        "questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("quiz_id", sa.Uuid(), nullable=False),
        sa.Column("stem", sa.Text(), nullable=False),
        sa.Column("options", JSON_TYPE, nullable=False),
        sa.Column("correct_option", sa.SmallInteger(), nullable=False),
        sa.Column("explanation", sa.Text(), server_default="", nullable=False),
        sa.Column("difficulty", question_difficulty, nullable=False),
        sa.Column("source_chunk_id", sa.Uuid(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["quiz_id"], ["quizzes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_chunk_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_questions_quiz_id", "questions", ["quiz_id"])
    op.create_index("ix_questions_source_chunk_id", "questions", ["source_chunk_id"])
    op.create_index("ix_questions_quiz_order", "questions", ["quiz_id", "order_index"])

    op.create_table(
        "quiz_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("quiz_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("max_score", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["quiz_id"], ["quizzes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_quiz_attempts_quiz_id", "quiz_attempts", ["quiz_id"])
    op.create_index("ix_quiz_attempts_student_id", "quiz_attempts", ["student_id"])
    op.create_index(
        "ix_quiz_attempts_student_quiz",
        "quiz_attempts",
        ["student_id", "quiz_id"],
    )
    op.create_index(
        "uq_active_attempt",
        "quiz_attempts",
        ["student_id", "quiz_id"],
        unique=True,
        postgresql_where=sa.text("submitted_at IS NULL"),
        sqlite_where=sa.text("submitted_at IS NULL"),
    )

    op.create_table(
        "attempt_answers",
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("selected_option", sa.SmallInteger(), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("time_spent_ms", sa.Integer(), nullable=True),
        sa.Column(
            "answered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["attempt_id"], ["quiz_attempts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("attempt_id", "question_id"),
    )


def downgrade() -> None:
    op.drop_table("attempt_answers")
    op.drop_index("uq_active_attempt", table_name="quiz_attempts")
    op.drop_index("ix_quiz_attempts_student_quiz", table_name="quiz_attempts")
    op.drop_index("ix_quiz_attempts_student_id", table_name="quiz_attempts")
    op.drop_index("ix_quiz_attempts_quiz_id", table_name="quiz_attempts")
    op.drop_table("quiz_attempts")
    op.drop_index("ix_questions_quiz_order", table_name="questions")
    op.drop_index("ix_questions_source_chunk_id", table_name="questions")
    op.drop_index("ix_questions_quiz_id", table_name="questions")
    op.drop_table("questions")
    op.drop_index("ix_quizzes_course_status", table_name="quizzes")
    op.drop_index("ix_quizzes_course_id", table_name="quizzes")
    op.drop_table("quizzes")
