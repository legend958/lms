"""Teacher analytics response DTOs.

Every number in these models is aggregated by SQL in ``analytics_service``;
the web client only renders what the API returns.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.core.roles import TaskProgressStatus, TopicReleaseState


class CohortKpis(BaseModel):
    students: int
    completion_rate: float
    pending: int
    completed: int
    avg_score: float | None
    active_today: int


class AnalyticsStudentRow(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    completion_rate: float
    completed: int
    pending: int
    last_active_at: datetime | None
    days_active: int
    avg_score: float | None
    at_risk: bool
    weak_topic_count: int


class AnalyticsStudentList(BaseModel):
    items: list[AnalyticsStudentRow]
    next_cursor: str | None = None


class TopicAnalytics(BaseModel):
    id: uuid.UUID
    title: str
    document_id: uuid.UUID
    document_title: str
    release_state: TopicReleaseState
    students: int
    completed: int
    pending: int
    completion_rate: float
    attempt_count: int
    avg_score: float | None
    question_accuracy: float | None


class KnowledgeGap(BaseModel):
    kind: Literal["topic", "question"]
    topic_id: uuid.UUID
    topic_title: str
    question_id: uuid.UUID | None = None
    question_stem: str | None = None
    accuracy: float
    answers: int


class AnalyticsTopics(BaseModel):
    items: list[TopicAnalytics]
    gaps: list[KnowledgeGap]


class AnalyticsStudentRef(BaseModel):
    id: uuid.UUID
    name: str
    email: str


class AnalyticsStudentSummary(BaseModel):
    completion_rate: float
    completed: int
    pending: int
    avg_score: float | None
    days_active: int
    last_active_at: datetime | None
    at_risk: bool
    weak_topic_count: int


class AnalyticsTaskHistoryItem(BaseModel):
    task_id: uuid.UUID
    title: str
    week_no: int
    due_at: datetime
    status: TaskProgressStatus
    completed_at: datetime | None


class AnalyticsAttemptItem(BaseModel):
    attempt_id: uuid.UUID
    quiz_id: uuid.UUID
    quiz_title: str
    topic_title: str
    started_at: datetime
    submitted_at: datetime | None
    score: int | None
    max_score: int | None
    percentage: float | None
    duration_seconds: int | None


class AnalyticsWeakQuestion(BaseModel):
    question_id: uuid.UUID
    topic_id: uuid.UUID
    topic_title: str
    stem: str
    selected_option: int | None
    correct_option: int
    explanation: str
    source_document: str | None
    source_page: int | None


class AnalyticsStudentDetail(BaseModel):
    student: AnalyticsStudentRef
    summary: AnalyticsStudentSummary
    tasks: list[AnalyticsTaskHistoryItem]
    attempts: list[AnalyticsAttemptItem]
    weakest_questions: list[AnalyticsWeakQuestion]
