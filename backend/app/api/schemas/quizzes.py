"""Quiz DTOs.

Teacher DTOs may include answer keys. Student DTOs never do: before submission
a student sees only the stem, options, and their own saved choices.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.roles import QuestionDifficulty, QuizGenerationStatus, QuizStatus


class QuizCourseRef(BaseModel):
    id: uuid.UUID
    code: str
    title: str


class TeacherQuestionOut(BaseModel):
    id: uuid.UUID
    quiz_id: uuid.UUID
    stem: str
    options: list[str]
    correct_option: int
    explanation: str
    difficulty: QuestionDifficulty
    source_chunk_id: uuid.UUID
    approved: bool
    order_index: int


class QuizOut(BaseModel):
    id: uuid.UUID
    topic_id: uuid.UUID
    course_id: uuid.UUID
    topic_title: str
    title: str
    pass_threshold: int
    attempt_limit: int
    status: QuizStatus
    auto_approve: bool
    needs_review: bool
    generation_status: QuizGenerationStatus
    generation_error: str | None
    question_count: int
    approved_count: int
    attempt_count: int
    created_at: datetime
    updated_at: datetime


class QuizDetail(QuizOut):
    questions: list[TeacherQuestionOut]


class QuizList(BaseModel):
    items: list[QuizOut]


class QuizCreate(BaseModel):
    auto_approve: bool = False
    title: str | None = Field(default=None, min_length=3, max_length=200)


class QuizUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    pass_threshold: int | None = Field(default=None, ge=0, le=100)
    attempt_limit: int | None = Field(default=None, ge=0, le=1000)
    auto_approve: bool | None = None


class QuestionUpdate(BaseModel):
    stem: str | None = Field(default=None, min_length=10, max_length=500)
    options: list[str] | None = Field(default=None, min_length=4, max_length=4)
    correct_option: int | None = Field(default=None, ge=0, le=3)
    explanation: str | None = Field(default=None, max_length=1000)
    difficulty: QuestionDifficulty | None = None
    approved: bool | None = None


class StudentQuestionOut(BaseModel):
    """Student-safe question projection. Never add answer-key fields here."""

    id: uuid.UUID
    stem: str
    options: list[str]
    order_index: int


class StudentQuizOut(BaseModel):
    id: uuid.UUID
    topic_id: uuid.UUID
    course: QuizCourseRef
    title: str
    question_count: int
    pass_threshold: int
    attempt_limit: int
    attempts_used: int
    best_score: int | None
    open_attempt_id: uuid.UUID | None


class StudentQuizDetail(BaseModel):
    quiz: StudentQuizOut
    questions: list[StudentQuestionOut]


class AnswerInput(BaseModel):
    question_id: uuid.UUID
    selected_option: int = Field(ge=0, le=3)
    time_spent_ms: int | None = Field(default=None, ge=0)


class AttemptSaveRequest(BaseModel):
    answers: list[AnswerInput] = Field(default_factory=list)


class AttemptSubmitRequest(BaseModel):
    answers: list[AnswerInput] = Field(default_factory=list)


class AttemptAnswerOut(BaseModel):
    question_id: uuid.UUID
    selected_option: int | None
    time_spent_ms: int | None


class StudentAttemptState(BaseModel):
    attempt_id: uuid.UUID
    quiz: StudentQuizOut
    started_at: datetime
    submitted_at: datetime | None
    questions: list[StudentQuestionOut]
    answers: list[AttemptAnswerOut]


class AttemptSaveResult(BaseModel):
    saved: int
    answered: int
    total: int


class AttemptReviewItem(BaseModel):
    question_id: uuid.UUID
    stem: str
    options: list[str]
    selected_option: int | None
    correct_option: int
    is_correct: bool
    explanation: str
    source_chunk_id: uuid.UUID


class StudentAttemptResult(BaseModel):
    attempt_id: uuid.UUID
    quiz_id: uuid.UUID
    quiz_title: str
    score: int
    max_score: int
    percentage: int
    passed: bool
    pass_threshold: int
    submitted_at: datetime
    review: list[AttemptReviewItem]


class StudentAttemptSummary(BaseModel):
    id: uuid.UUID
    quiz_id: uuid.UUID
    quiz_title: str
    course: QuizCourseRef
    started_at: datetime
    submitted_at: datetime | None
    score: int | None
    max_score: int | None
    percentage: int | None
    passed: bool
    open: bool


class StudentAttemptList(BaseModel):
    items: list[StudentAttemptSummary]
    next_cursor: str | None = None
