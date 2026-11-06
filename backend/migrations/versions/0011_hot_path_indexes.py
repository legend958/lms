"""hot-path indexes for attempt history and knowledge gaps

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-05

``attempt_answers`` is keyed by ``(attempt_id, question_id)``. The teacher
knowledge-gap report filters and joins by ``question_id`` alone, so without a
leading index Postgres has to scan the whole table once per question.

``quiz_attempts`` already has ``(student_id, quiz_id)`` for per-quiz lookups,
but the student history endpoint lists every attempt for a student ordered by
``started_at``. A ``(student_id, started_at)`` index serves that sort directly.
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_attempt_answers_question_id",
        "attempt_answers",
        ["question_id"],
        unique=False,
    )
    op.create_index(
        "ix_quiz_attempts_student_started",
        "quiz_attempts",
        ["student_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_quiz_attempts_student_started", table_name="quiz_attempts")
    op.drop_index("ix_attempt_answers_question_id", table_name="attempt_answers")
