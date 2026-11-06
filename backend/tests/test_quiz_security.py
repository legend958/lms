"""Quiz authorization and answer-key leakage tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import QuizStatus, TopicReleaseState
from tests.conftest import login
from tests.quiz_utils import seed_quiz

FORBIDDEN_STUDENT_KEYS = {"correct_option", "explanation", "source_chunk_id"}


def assert_no_answer_key(value: object) -> None:
    if isinstance(value, dict):
        assert not (FORBIDDEN_STUDENT_KEYS & set(value)), value
        for item in value.values():
            assert_no_answer_key(item)
    elif isinstance(value, list):
        for item in value:
            assert_no_answer_key(item)


async def _start(client, quiz_id: uuid.UUID) -> dict:  # type: ignore[no-untyped-def]
    response = await client.post(f"/api/v1/student/quizzes/{quiz_id}/attempts")
    assert response.status_code == 200, response.text
    return response.json()


async def test_student_payloads_never_contain_the_answer_key(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(session, world, question_count=3)
    student = await client_factory()
    await login(student, world.student.email)

    listing = await student.get("/api/v1/student/quizzes")
    assert listing.status_code == 200, listing.text
    assert_no_answer_key(listing.json())

    detail = await student.get(f"/api/v1/student/quizzes/{seeded.quiz.id}")
    assert detail.status_code == 200, detail.text
    assert_no_answer_key(detail.json())

    state = await _start(student, seeded.quiz.id)
    assert_no_answer_key(state)
    attempt_id = state["attempt_id"]
    loaded = await student.get(f"/api/v1/student/attempts/{attempt_id}")
    assert loaded.status_code == 200, loaded.text
    assert_no_answer_key(loaded.json())

    submitted = await student.post(
        f"/api/v1/student/attempts/{attempt_id}/submit",
        json={"answers": []},
    )
    assert submitted.status_code == 200, submitted.text
    # After submission the review intentionally includes the key and citations.
    review = submitted.json()["review"][0]
    assert {"correct_option", "explanation", "source_chunk_id"} <= set(review)


async def test_other_student_cannot_see_quiz_or_attempt(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(session, world, question_count=2)
    student = await client_factory()
    await login(student, world.student.email)
    state = await _start(student, seeded.quiz.id)
    attempt_id = state["attempt_id"]

    outsider = await client_factory()
    await login(outsider, world.student_b.email)

    listing = await outsider.get("/api/v1/student/quizzes")
    assert listing.status_code == 200
    assert listing.json() == []
    assert (await outsider.get(f"/api/v1/student/quizzes/{seeded.quiz.id}")).status_code == 404
    assert (
        await outsider.post(f"/api/v1/student/quizzes/{seeded.quiz.id}/attempts")
    ).status_code == 404
    assert (await outsider.get(f"/api/v1/student/attempts/{attempt_id}")).status_code == 404
    assert (
        await outsider.patch(
            f"/api/v1/student/attempts/{attempt_id}",
            json={"answers": []},
        )
    ).status_code == 404
    assert (
        await outsider.post(
            f"/api/v1/student/attempts/{attempt_id}/submit",
            json={"answers": []},
        )
    ).status_code == 404
    assert (await outsider.get(f"/api/v1/student/attempts/{attempt_id}/result")).status_code == 404


async def test_draft_and_unreleased_quizzes_are_invisible(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    draft = await seed_quiz(
        session,
        world,
        topic_title="Draft Topic",
        question_count=2,
        status=QuizStatus.DRAFT,
        approved=False,
    )
    published = await seed_quiz(
        session,
        world,
        topic_title="Hidden Topic",
        question_count=2,
        status=QuizStatus.PUBLISHED,
    )
    student = await client_factory()
    await login(student, world.student.email)

    assert (await student.get(f"/api/v1/student/quizzes/{draft.quiz.id}")).status_code == 404
    assert (
        await student.post(f"/api/v1/student/quizzes/{draft.quiz.id}/attempts")
    ).status_code == 404

    assert (await student.get(f"/api/v1/student/quizzes/{published.quiz.id}")).status_code == 200
    published.topic.release_state = TopicReleaseState.DRAFT
    published.topic.released_at = None
    await session.commit()

    assert (await student.get(f"/api/v1/student/quizzes/{published.quiz.id}")).status_code == 404
    assert (
        await student.post(f"/api/v1/student/quizzes/{published.quiz.id}/attempts")
    ).status_code == 404


async def test_deleted_document_hides_the_quiz(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(session, world, question_count=2)
    seeded.document.document.deleted_at = datetime.now(UTC)
    await session.commit()

    student = await client_factory()
    await login(student, world.student.email)
    assert (await student.get("/api/v1/student/quizzes")).json() == []
    assert (await student.get(f"/api/v1/student/quizzes/{seeded.quiz.id}")).status_code == 404


async def test_roles_cannot_cross_portals(client_factory, world, session: AsyncSession) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(session, world, question_count=2)
    student = await client_factory()
    await login(student, world.student.email)
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    assert (await student.get("/api/v1/teacher/quizzes")).status_code == 403
    assert (
        await student.post(f"/api/v1/teacher/quizzes/{seeded.quiz.id}/approve")
    ).status_code == 403
    assert (await teacher.get("/api/v1/student/quizzes")).status_code == 403


async def test_teacher_cannot_mutate_another_courses_quiz(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(
        session,
        world,
        question_count=2,
        status=QuizStatus.DRAFT,
        approved=False,
    )
    other_teacher = await client_factory()
    await login(other_teacher, world.teacher_b.email)

    question_id = seeded.questions[0].id
    assert (
        await other_teacher.patch(
            f"/api/v1/teacher/questions/{question_id}",
            json={"stem": "A stolen question stem for review?"},
        )
    ).status_code == 404
    assert (
        await other_teacher.delete(f"/api/v1/teacher/questions/{question_id}")
    ).status_code == 404
    assert (
        await other_teacher.post(f"/api/v1/teacher/quizzes/{seeded.quiz.id}/approve")
    ).status_code == 404
    assert (
        await other_teacher.post(f"/api/v1/teacher/quizzes/{seeded.quiz.id}/regenerate")
    ).status_code == 404


async def test_student_course_filter_is_scoped(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    await seed_quiz(session, world, question_count=2)
    student = await client_factory()
    await login(student, world.student.email)

    own = await student.get(f"/api/v1/student/quizzes?course_id={world.course_a.id}")
    assert own.status_code == 200
    assert len(own.json()) == 1

    foreign = await student.get(f"/api/v1/student/quizzes?course_id={world.course_b.id}")
    assert foreign.status_code == 404
