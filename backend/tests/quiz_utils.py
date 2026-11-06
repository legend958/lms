"""Seed quizzes, questions, and released topics without the LLM."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import (
    QuestionDifficulty,
    QuizGenerationStatus,
    QuizStatus,
    TopicReleaseState,
)
from app.db.models import Question, Quiz, Topic
from tests.topic_utils import SeededDocument, seed_document


@dataclass
class SeededQuiz:
    quiz: Quiz
    topic: Topic
    document: SeededDocument
    questions: list[Question] = field(default_factory=list)


DEFAULT_OPTIONS = ["Alpha", "Beta", "Gamma", "Delta"]


async def seed_quiz(
    session: AsyncSession,
    world,
    *,
    seeded_document: SeededDocument | None = None,
    topic_title: str = "Atoms",
    question_count: int = 4,
    status: QuizStatus = QuizStatus.PUBLISHED,
    approved: bool = True,
    attempt_limit: int = 0,
    pass_threshold: int = 60,
    auto_approve: bool = False,
) -> SeededQuiz:
    """Create a released topic with a quiz and deterministic questions.

    Correct options cycle through ``0, 1, 2, 3`` so grading tests can compute an
    expected score by hand.
    """
    document = seeded_document or await seed_document(
        session,
        world,
        topic_specs=[{"title": topic_title, "chunk_count": 2, "released": True}],
    )
    topic = document.named(topic_title).topic
    if topic.release_state is not TopicReleaseState.RELEASED:
        topic.release_state = TopicReleaseState.RELEASED
        topic.released_by = world.teacher.id
        topic.released_at = datetime.now(UTC)
        for chunk in document.named(topic_title).chunks:
            chunk.released = True
        await session.commit()

    quiz = Quiz(
        topic_id=topic.id,
        course_id=topic.course_id,
        title=f"{topic.title} quiz",
        pass_threshold=pass_threshold,
        attempt_limit=attempt_limit,
        status=status,
        auto_approve=auto_approve,
        needs_review=status is QuizStatus.DRAFT,
        generation_status=QuizGenerationStatus.READY,
    )
    session.add(quiz)
    await session.flush()

    chunks = document.named(topic_title).chunks
    questions: list[Question] = []
    for index in range(question_count):
        question = Question(
            quiz_id=quiz.id,
            stem=f"{topic.title} question {index + 1}?",
            options=list(DEFAULT_OPTIONS),
            correct_option=index % 4,
            explanation=f"Explanation for question {index + 1}",
            difficulty=QuestionDifficulty.MEDIUM,
            source_chunk_id=chunks[index % len(chunks)].id,
            approved=approved,
            order_index=index,
        )
        session.add(question)
        questions.append(question)

    await session.commit()
    return SeededQuiz(
        quiz=quiz,
        topic=topic,
        document=document,
        questions=questions,
    )
