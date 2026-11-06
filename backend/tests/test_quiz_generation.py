"""Quiz generation, release wiring, and teacher review tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import (
    QuestionDifficulty,
    QuizGenerationStatus,
    QuizStatus,
)
from app.db.models import Question, Quiz
from app.rag.quizzes import (
    GeneratedQuestion,
    QuizGenerationResult,
    QuizSourceChunk,
    QuizValidationError,
    validate_question,
)
from app.services.quiz_service import run_quiz_generation
from tests.conftest import login
from tests.quiz_utils import seed_quiz
from tests.topic_utils import seed_document

DUE_AT = datetime(2026, 12, 1, 23, 59, 59, tzinfo=UTC).isoformat()


class FakeGenerator:
    def __init__(self, questions: list[GeneratedQuestion]) -> None:
        self.questions = questions

    async def generate(self, chunks, target: int) -> QuizGenerationResult:  # type: ignore[no-untyped-def]
        del chunks, target
        return QuizGenerationResult(questions=tuple(self.questions), rejected=0, rounds=1)


def generated_questions(chunks, count: int) -> list[GeneratedQuestion]:  # type: ignore[no-untyped-def]
    return [
        GeneratedQuestion(
            stem=f"Generated question number {index + 1}?",
            options=("Alpha", "Beta", "Gamma", "Delta"),
            correct_option=index % 4,
            explanation="Because the source says so.",
            difficulty=QuestionDifficulty.MEDIUM,
            source_chunk_id=chunks[index % len(chunks)].id,
        )
        for index in range(count)
    ]


async def _questions(session: AsyncSession, quiz_id: uuid.UUID) -> list[Question]:
    stmt = select(Question).where(Question.quiz_id == quiz_id).order_by(Question.order_index)
    return list((await session.execute(stmt)).scalars())


async def test_generation_persists_grounded_draft_questions(world, session: AsyncSession) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(
        session,
        world,
        question_count=0,
        status=QuizStatus.DRAFT,
        approved=False,
    )
    chunks = seeded.document.named("Atoms").chunks
    await run_quiz_generation(
        seeded.quiz.id,
        generator=FakeGenerator(generated_questions(chunks, 6)),
    )

    await session.refresh(seeded.quiz)
    assert seeded.quiz.generation_status is QuizGenerationStatus.READY
    assert seeded.quiz.status is QuizStatus.DRAFT
    assert seeded.quiz.needs_review is True
    assert seeded.quiz.generation_error is None

    questions = await _questions(session, seeded.quiz.id)
    assert len(questions) == 6
    assert [question.order_index for question in questions] == list(range(6))
    assert all(question.approved is False for question in questions)
    chunk_ids = {chunk.id for chunk in chunks}
    assert all(question.source_chunk_id in chunk_ids for question in questions)


async def test_generation_replaces_previous_draft_questions(world, session: AsyncSession) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(
        session,
        world,
        question_count=3,
        status=QuizStatus.DRAFT,
        approved=False,
    )
    chunks = seeded.document.named("Atoms").chunks
    await run_quiz_generation(
        seeded.quiz.id,
        generator=FakeGenerator(generated_questions(chunks, 5)),
    )

    questions = await _questions(session, seeded.quiz.id)
    assert len(questions) == 5
    assert all(question.stem.startswith("Generated question") for question in questions)


async def test_generation_fails_when_below_minimum(world, session: AsyncSession) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(
        session,
        world,
        question_count=0,
        status=QuizStatus.DRAFT,
        approved=False,
    )
    chunks = seeded.document.named("Atoms").chunks
    await run_quiz_generation(
        seeded.quiz.id,
        generator=FakeGenerator(generated_questions(chunks, 2)),
    )

    await session.refresh(seeded.quiz)
    assert seeded.quiz.generation_status is QuizGenerationStatus.FAILED
    assert "Only 2 valid questions" in (seeded.quiz.generation_error or "")
    assert await _questions(session, seeded.quiz.id) == []


async def test_auto_approve_publishes_when_enough_questions(world, session: AsyncSession) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(
        session,
        world,
        question_count=0,
        status=QuizStatus.DRAFT,
        approved=False,
        auto_approve=True,
    )
    chunks = seeded.document.named("Atoms").chunks
    await run_quiz_generation(
        seeded.quiz.id,
        generator=FakeGenerator(generated_questions(chunks, 5)),
    )

    await session.refresh(seeded.quiz)
    assert seeded.quiz.status is QuizStatus.PUBLISHED
    assert seeded.quiz.needs_review is False
    questions = await _questions(session, seeded.quiz.id)
    assert len(questions) == 5
    assert all(question.approved is True for question in questions)


async def test_generation_skips_published_quiz(world, session: AsyncSession) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(session, world, question_count=4)
    chunks = seeded.document.named("Atoms").chunks
    await run_quiz_generation(
        seeded.quiz.id,
        generator=FakeGenerator(generated_questions(chunks, 6)),
    )

    questions = await _questions(session, seeded.quiz.id)
    assert len(questions) == 4
    assert all(question.stem.startswith("Atoms question") for question in questions)


def test_validate_question_rejects_bad_proposals() -> None:
    chunk = QuizSourceChunk(
        id=uuid.uuid4(),
        text="Atoms are the basic building blocks of matter.",
        page_start=1,
        page_end=1,
        section_title="Atoms",
    )
    chunks = {chunk.id: chunk}
    base = {
        "stem": "What are atoms the basic building blocks of?",
        "options": ["Matter", "Energy", "Light", "Sound"],
        "correct_index": 0,
        "explanation": "The excerpt says so.",
        "difficulty": "medium",
        "source_chunk_id": str(chunk.id),
    }
    seen: set[str] = set()
    question = validate_question(base, chunks, seen)
    assert question.correct_option == 0

    for broken in (
        {**base, "options": ["Matter", "Energy", "Light"]},
        {**base, "options": ["Matter", "Matter", "Light", "Sound"]},
        {**base, "correct_index": 4},
        {**base, "source_chunk_id": str(uuid.uuid4())},
        {**base, "options": ["Matter", "Not matter", "Light", "Sound"]},
    ):
        with pytest.raises(QuizValidationError):
            validate_question(broken, chunks, set())

    seen.add("what are atoms the basic building blocks of")
    with pytest.raises(QuizValidationError):
        validate_question(base, chunks, seen)


async def test_release_creates_a_quiz_by_default(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Atoms", "chunk_count": 2}],
    )
    topic = seeded.named("Atoms").topic
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    response = await teacher.post(
        f"/api/v1/topics/{topic.id}/release",
        json={"week_no": 1, "due_at": DUE_AT},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quiz_id"]
    assert body["quiz_queued"] is False

    quiz = (await session.execute(select(Quiz).where(Quiz.topic_id == topic.id))).scalar_one()
    assert str(quiz.id) == body["quiz_id"]
    assert quiz.status is QuizStatus.DRAFT
    assert quiz.generation_status is QuizGenerationStatus.QUEUED

    repeated = await teacher.post(
        f"/api/v1/topics/{topic.id}/release",
        json={"week_no": 2, "due_at": DUE_AT},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["quiz_id"] == body["quiz_id"]


async def test_release_can_skip_quiz_generation(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": "Atoms", "chunk_count": 2}],
    )
    topic = seeded.named("Atoms").topic
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    response = await teacher.post(
        f"/api/v1/topics/{topic.id}/release",
        json={"week_no": 1, "due_at": DUE_AT, "generate_quiz": False},
    )
    assert response.status_code == 200, response.text
    assert response.json()["quiz_id"] is None
    stmt = select(Quiz).where(Quiz.topic_id == topic.id)
    assert (await session.execute(stmt)).scalar_one_or_none() is None


async def test_teacher_can_edit_and_approve_a_quiz(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(
        session,
        world,
        question_count=4,
        status=QuizStatus.DRAFT,
        approved=False,
    )
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    detail = await teacher.get(f"/api/v1/teacher/quizzes/{seeded.quiz.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "draft"
    assert body["question_count"] == 4
    assert body["approved_count"] == 0
    assert all(question["correct_option"] is not None for question in body["questions"])

    question_id = body["questions"][0]["id"]
    edited = await teacher.patch(
        f"/api/v1/teacher/questions/{question_id}",
        json={"stem": "An edited question stem for review?"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["approved"] is False
    assert edited.json()["stem"] == "An edited question stem for review?"

    approved = await teacher.post(f"/api/v1/teacher/quizzes/{seeded.quiz.id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "published"
    assert approved.json()["approved_count"] == 4

    student = await client_factory()
    await login(student, world.student.email)
    listing = await student.get("/api/v1/student/quizzes")
    assert listing.status_code == 200, listing.text
    assert [item["title"] for item in listing.json()] == ["Atoms quiz"]

    student_detail = await student.get(f"/api/v1/student/quizzes/{seeded.quiz.id}")
    assert student_detail.status_code == 200, student_detail.text
    question = student_detail.json()["questions"][0]
    assert set(question) == {"id", "stem", "options", "order_index"}


async def test_regenerate_is_rejected_for_published_quiz(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(session, world)
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    response = await teacher.post(f"/api/v1/teacher/quizzes/{seeded.quiz.id}/regenerate")
    assert response.status_code == 409


async def test_teacher_quiz_is_scoped_to_the_course(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(session, world)
    other_teacher = await client_factory()
    await login(other_teacher, world.teacher_b.email)

    response = await other_teacher.get(f"/api/v1/teacher/quizzes/{seeded.quiz.id}")
    assert response.status_code == 404
    listing = await other_teacher.get("/api/v1/teacher/quizzes")
    assert listing.status_code == 200
    assert listing.json()["items"] == []
