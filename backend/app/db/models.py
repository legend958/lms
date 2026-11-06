"""ORM models for identity, courses, announcements, audit, and ingestion."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.roles import (
    CourseRole,
    CourseStatus,
    DocumentStatus,
    IngestStage,
    NotificationType,
    QuestionDifficulty,
    QuizGenerationStatus,
    QuizStatus,
    Role,
    TaskProgressStatus,
    TaskStatus,
    TopicReleaseState,
    TopicSource,
    UserStatus,
)
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

JSONType = JSON().with_variant(JSONB, "postgresql")


def _enum(enum_cls: type, name: str) -> Enum:
    """Store lowercase enum *values*, portable across Postgres and SQLite."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=20,
        validate_strings=True,
        values_callable=lambda cls: [member.value for member in cls],
    )


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(
        _enum(Role, "user_role"), default=Role.STUDENT, nullable=False
    )
    status: Mapped[UserStatus] = mapped_column(
        _enum(UserStatus, "user_status"), default=UserStatus.ACTIVE, nullable=False
    )
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_users_role_status", "role", "status"),
        Index("ix_users_last_active_at", "last_active_at"),
    )


class Course(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "courses"

    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    term: Mapped[str | None] = mapped_column(String(60), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[CourseStatus] = mapped_column(
        _enum(CourseStatus, "course_status"), default=CourseStatus.ACTIVE, nullable=False
    )

    __table_args__ = (Index("ix_courses_status", "status"),)


class Enrollment(Base):
    __tablename__ = "enrollments"

    course_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    course_role: Mapped[CourseRole] = mapped_column(
        _enum(CourseRole, "course_role"), default=CourseRole.STUDENT, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (Index("ix_enrollments_user_course", "user_id", "course_id"),)


class Announcement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "announcements"

    course_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="CASCADE"), nullable=True, index=True
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    publish_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_announcements_course_publish", "course_id", "publish_at"),)


class AnnouncementRead(Base):
    __tablename__ = "announcement_reads"

    announcement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("announcements.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_logs"

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)


class Permission(Base, TimestampMixin):
    """Role capability flag. Admins always bypass these rows."""

    __tablename__ = "permissions"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    role: Mapped[Role] = mapped_column(_enum(Role, "permission_role"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RefreshToken(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Document(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "documents"

    course_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus, "document_status"),
        default=DocumentStatus.QUEUED,
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    topic_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    topics_source: Mapped[TopicSource | None] = mapped_column(
        _enum(TopicSource, "topic_source"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_documents_course_checksum_active",
            "course_id",
            "checksum",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_documents_course_status", "course_id", "status"),
    )


class IngestionJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ingestion_jobs"

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[IngestStage] = mapped_column(
        _enum(IngestStage, "ingest_stage"), default=IngestStage.QUEUED, nullable=False
    )
    progress_pct: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_ingestion_jobs_document_created", "document_id", "created_at"),)


class Chunk(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "chunks"

    vector_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    section_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    released: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_index"),
        Index("ix_chunks_course_released", "course_id", "released"),
        Index("ix_chunks_topic_released", "topic_id", "released"),
    )


class Topic(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "topics"

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    release_state: Mapped[TopicReleaseState] = mapped_column(
        _enum(TopicReleaseState, "topic_release_state"),
        default=TopicReleaseState.DRAFT,
        nullable=False,
    )
    released_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_topics_document_release", "document_id", "release_state"),
        Index("ix_topics_course_release", "course_id", "release_state"),
        Index("ix_topics_document_order", "document_id", "order_index"),
    )


class Task(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tasks"

    topic_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    week_no: Mapped[int] = mapped_column(Integer, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        _enum(TaskStatus, "task_status"), default=TaskStatus.DRAFT, nullable=False
    )

    __table_args__ = (Index("ix_tasks_course_due", "course_id", "due_at"),)


class TaskProgress(Base):
    """Per-student completion state. Rows survive topic unrelease on purpose."""

    __tablename__ = "task_progress"

    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[TaskProgressStatus] = mapped_column(
        _enum(TaskProgressStatus, "task_progress_status"),
        default=TaskProgressStatus.INCOMPLETE,
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (Index("ix_task_progress_student_status", "student_id", "status"),)


class Quiz(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One quiz per topic. Generated as a draft; a teacher publishes it."""

    __tablename__ = "quizzes"

    topic_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    pass_threshold: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    # 0 means unlimited, matching the PRD default.
    attempt_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[QuizStatus] = mapped_column(
        _enum(QuizStatus, "quiz_status"), default=QuizStatus.DRAFT, nullable=False
    )
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    generation_status: Mapped[QuizGenerationStatus] = mapped_column(
        _enum(QuizGenerationStatus, "quiz_generation_status"),
        default=QuizGenerationStatus.QUEUED,
        nullable=False,
    )
    generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_quizzes_course_status", "course_id", "status"),)


class Question(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """MCQ only: exactly four options and one correct index (0-3)."""

    __tablename__ = "questions"

    quiz_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    correct_option: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    difficulty: Mapped[QuestionDifficulty] = mapped_column(
        _enum(QuestionDifficulty, "question_difficulty"),
        default=QuestionDifficulty.MEDIUM,
        nullable=False,
    )
    source_chunk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (Index("ix_questions_quiz_order", "quiz_id", "order_index"),)


class QuizAttempt(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "quiz_attempts"

    quiz_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_quiz_attempts_student_quiz", "student_id", "quiz_id"),
        Index("ix_quiz_attempts_student_started", "student_id", "started_at"),
        Index(
            "uq_active_attempt",
            "student_id",
            "quiz_id",
            unique=True,
            sqlite_where=text("submitted_at IS NULL"),
            postgresql_where=text("submitted_at IS NULL"),
        ),
    )


class AttemptAnswer(Base):
    """One row per question in an attempt, including unanswered questions."""

    __tablename__ = "attempt_answers"

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("quiz_attempts.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )
    selected_option: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    time_spent_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (Index("ix_attempt_answers_question_id", "question_id"),)


class RagQueryLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "rag_query_logs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_ids: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    max_similarity: Mapped[float] = mapped_column(Float, nullable=False)
    refusal: Mapped[bool] = mapped_column(Boolean, nullable=False)
    injection_flag: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_rag_query_logs_user_created", "user_id", "created_at"),
        Index("ix_rag_query_logs_course_created", "course_id", "created_at"),
    )


class Notification(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[NotificationType] = mapped_column(
        _enum(NotificationType, "notification_type"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_user_read", "user_id", "read_at"),
    )
