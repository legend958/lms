"""Student attempt lifecycle: start/resume, autosave, submit, grading, history."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import TopicReleaseState
from app.db.models import AttemptAnswer
from tests.conftest import login
from tests.quiz_utils import seed_quiz


async def _start(client, quiz_id: uuid.UUID) -> dict:  # type: ignore[no-untyped-def]
    response = await client.post(f"/api/v1/student/quizzes/{quiz_id}/attempts")
    assert response.status_code == 200, response.text
    return response.json()


async def test_start_save_submit_and_grade(client_factory, world, session: AsyncSession) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(session, world, question_count=4, pass_threshold=60)
    student = await client_factory()
    await login(student, world.student.email)

    state = await _start(student, seeded.quiz.id)
    attempt_id = state["attempt_id"]
    assert state["submitted_at"] is None
    assert len(state["questions"]) == 4
    assert state["answers"] == []
    assert state["quiz"]["open_attempt_id"] == attempt_id

    resumed = await _start(student, seeded.quiz.id)
    assert resumed["attempt_id"] == attempt_id

    q0, q1, q2, q3 = [question["id"] for question in state["questions"]]
    saved = await student.patch(
        f"/api/v1/student/attempts/{attempt_id}",
        json={
            "answers": [
                {"question_id": q0, "selected_option": 0},
                {"question_id": q1, "selected_option": 0},
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"saved": 2, "answered": 2, "total": 4}

    loaded = await student.get(f"/api/v1/student/attempts/{attempt_id}")
    assert loaded.status_code == 200, loaded.text
    saved_answers = {
        item["question_id"]: item["selected_option"] for item in loaded.json()["answers"]
    }
    assert saved_answers[q0] == 0
    assert saved_answers[q1] == 0

    submitted = await student.post(
        f"/api/v1/student/attempts/{attempt_id}/submit",
        json={"answers": [{"question_id": q2, "selected_option": 2}]},
    )
    assert submitted.status_code == 200, submitted.text
    result = submitted.json()
    assert result["score"] == 2
    assert result["max_score"] == 4
    assert result["percentage"] == 50
    assert result["passed"] is False
    review = {item["question_id"]: item for item in result["review"]}
    assert review[q0]["is_correct"] is True
    assert review[q1]["is_correct"] is False
    assert review[q2]["is_correct"] is True
    assert review[q3]["selected_option"] is None
    assert review[q3]["is_correct"] is False
    assert review[q0]["correct_option"] == 0
    assert review[q0]["explanation"]

    rows = list(
        (
            await session.execute(
                select(AttemptAnswer).where(AttemptAnswer.attempt_id == uuid.UUID(attempt_id))
            )
        ).scalars()
    )
    assert len(rows) == 4
    by_question = {str(row.question_id): row for row in rows}
    assert by_question[q3].selected_option is None
    assert by_question[q3].is_correct is False

    blocked = await student.patch(
        f"/api/v1/student/attempts/{attempt_id}",
        json={"answers": [{"question_id": q0, "selected_option": 1}]},
    )
    assert blocked.status_code == 409

    repeated = await student.post(
        f"/api/v1/student/attempts/{attempt_id}/submit",
        json={"answers": [{"question_id": q0, "selected_option": 1}]},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["score"] == 2
    assert repeated.json()["submitted_at"] == result["submitted_at"]

    stored = await student.get(f"/api/v1/student/attempts/{attempt_id}/result")
    assert stored.status_code == 200, stored.text
    assert stored.json()["score"] == 2


async def test_attempt_limit_is_enforced_after_submit(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(session, world, question_count=4, attempt_limit=1)
    student = await client_factory()
    await login(student, world.student.email)

    state = await _start(student, seeded.quiz.id)
    attempt_id = state["attempt_id"]
    answers = [
        {"question_id": question["id"], "selected_option": index % 4}
        for index, question in enumerate(state["questions"])
    ]
    submitted = await student.post(
        f"/api/v1/student/attempts/{attempt_id}/submit",
        json={"answers": answers},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["percentage"] == 100

    blocked = await student.post(f"/api/v1/student/quizzes/{seeded.quiz.id}/attempts")
    assert blocked.status_code == 409


async def test_attempt_history_lists_open_and_submitted_attempts(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(session, world, question_count=2)
    student = await client_factory()
    await login(student, world.student.email)

    state = await _start(student, seeded.quiz.id)
    history = await student.get("/api/v1/student/attempts")
    assert history.status_code == 200, history.text
    item = history.json()["items"][0]
    assert item["open"] is True
    assert item["score"] is None

    attempt_id = state["attempt_id"]
    submitted = await student.post(
        f"/api/v1/student/attempts/{attempt_id}/submit",
        json={
            "answers": [
                {"question_id": state["questions"][0]["id"], "selected_option": 0},
                {"question_id": state["questions"][1]["id"], "selected_option": 1},
            ]
        },
    )
    assert submitted.status_code == 200, submitted.text

    history = await student.get("/api/v1/student/attempts")
    item = history.json()["items"][0]
    assert item["open"] is False
    assert item["score"] == 2
    assert item["percentage"] == 100
    assert item["passed"] is True
    assert item["submitted_at"]


async def test_result_requires_submission_and_unknown_answers_are_rejected(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(session, world, question_count=2)
    student = await client_factory()
    await login(student, world.student.email)

    state = await _start(student, seeded.quiz.id)
    attempt_id = state["attempt_id"]

    early = await student.get(f"/api/v1/student/attempts/{attempt_id}/result")
    assert early.status_code == 409

    rejected = await student.patch(
        f"/api/v1/student/attempts/{attempt_id}",
        json={"answers": [{"question_id": str(uuid.uuid4()), "selected_option": 0}]},
    )
    assert rejected.status_code == 422


async def test_submitted_attempt_survives_quiz_unrelease_for_history(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    seeded = await seed_quiz(session, world, question_count=2)
    student = await client_factory()
    await login(student, world.student.email)

    state = await _start(student, seeded.quiz.id)
    attempt_id = state["attempt_id"]
    await student.post(
        f"/api/v1/student/attempts/{attempt_id}/submit",
        json={"answers": []},
    )

    seeded.topic.release_state = TopicReleaseState.DRAFT
    seeded.topic.released_at = None
    await session.commit()

    listing = await student.get("/api/v1/student/quizzes")
    assert listing.status_code == 200
    assert listing.json() == []

    hidden = await student.get(f"/api/v1/student/quizzes/{seeded.quiz.id}")
    assert hidden.status_code == 404

    result = await student.get(f"/api/v1/student/attempts/{attempt_id}/result")
    assert result.status_code == 200, result.text
    assert result.json()["score"] == 0
    assert result.json()["max_score"] == 2


def test_datetime_helpers_are_timezone_aware() -> None:
    from app.services.attempt_service import _as_utc

    naive = datetime(2026, 1, 1, 12, 0)
    assert _as_utc(naive) == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
