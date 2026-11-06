"""Enumerations shared by models, schemas, and services."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    STUDENT = "student"
    TEACHER = "teacher"
    ADMIN = "admin"


class UserStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"


class CourseStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class CourseRole(StrEnum):
    STUDENT = "student"
    TEACHER = "teacher"


class DocumentStatus(StrEnum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXED = "indexed"
    TOPIC_EXTRACTION = "topic_extraction"
    READY = "ready"
    FAILED = "failed"


class IngestStage(StrEnum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXED = "indexed"
    TOPIC_EXTRACTION = "topic_extraction"
    READY = "ready"
    FAILED = "failed"


class IngestErrorCode(StrEnum):
    ENCRYPTED_PDF = "ENCRYPTED_PDF"
    NO_TEXT_LAYER = "NO_TEXT_LAYER"
    VECTOR_WRITE_FAILED = "VECTOR_WRITE_FAILED"
    PARSE_FAILED = "PARSE_FAILED"
    EMBEDDING_FAILED = "EMBEDDING_FAILED"
    STORAGE_FAILED = "STORAGE_FAILED"
    QUEUE_UNAVAILABLE = "QUEUE_UNAVAILABLE"
    RELEASED_TOPICS = "RELEASED_TOPICS"
    UNKNOWN = "UNKNOWN"


class TopicReleaseState(StrEnum):
    DRAFT = "draft"
    RELEASED = "released"
    SCHEDULED = "scheduled"


class TopicSource(StrEnum):
    LLM = "llm"
    FALLBACK = "fallback"


class TaskStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class TaskProgressStatus(StrEnum):
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"


class QuizStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class QuizGenerationStatus(StrEnum):
    QUEUED = "queued"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class QuestionDifficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class NotificationType(StrEnum):
    TASK_RELEASED = "task_released"


TERMINAL_DOCUMENT_STATUSES = frozenset(
    {DocumentStatus.INDEXED, DocumentStatus.READY, DocumentStatus.FAILED}
)
