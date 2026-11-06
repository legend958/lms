"""Teacher analytics: exact fixture numbers, ownership, and cache invalidation.

The fixture is deliberately hand-computable: 10 students, 3 released topics,
one published task per topic, and two submitted attempts per student/topic.
Scores follow simple index-based rules so every aggregate below can be checked
by hand and against independent SQL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import TaskProgressStatus
from app.db.models import AttemptAnswer, QuizAttempt, TaskProgress, User
from app.db.repositories import courses as courses_repo
from tests.conftest import login, make_user
from tests.quiz_utils import seed_quiz
from tests.topic_utils import seed_document, seed_published_task

TOPICS = ("Topic A", "Topic B", "Topic C")
MAX_SCORE = 4

# Two attempt scores (out of 4) for each student index.
SCORES: dict[str, tuple[int, int]] = {
    "Topic A": (4, 4),
    "Topic B": (2, 1),
    "Topic C": (1, 1),
}
SCORES_HIGH: dict[str, tuple[int, int]] = {
    "Topic A": (4, 4),
    "Topic B": (4, 3),
    "Topic C": (3, 3),
}
SCORES_LOW_A: dict[str, tuple[int, int]] = {
    "Topic A": (2, 2),
    "Topic B": (2, 1),
    "Topic C": (1, 1),
}


def _scores_for(topic: str, student_index: int) -> tuple[int, int]:
    if topic == "Topic A":
        return SCORES["Topic A"] if student_index < 8 else SCORES_LOW_A["Topic A"]
    if topic == "Topic B":
        return SCORES["Topic B"] if student_index < 7 else SCORES_HIGH["Topic B"]
    return SCORES["Topic C"] if student_index < 5 else SCORES_HIGH["Topic C"]


async def _seed_fixture(session: AsyncSession, world) -> SimpleNamespace:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    existing = await courses_repo.get_enrollment(session, world.course_a.id, world.student.id)
    if existing is not None:
        await courses_repo.unenroll(session, existing)
        await session.commit()

    students = []
    for index in range(10):
        student = await make_user(
            f"analytics{index}@example.com",
            name=f"Student {index:02d}",
        )
        await courses_repo.enroll(session, world.course_a.id, student.id)
        await session.execute(
            update(User)
            .where(User.id == student.id)
            .values(last_active_at=now if index < 4 else now - timedelta(days=2))
        )
        students.append(student)
    await session.commit()

    seeded = await seed_document(
        session,
        world,
        topic_specs=[{"title": topic, "chunk_count": 2, "released": True} for topic in TOPICS],
    )
    tasks: dict[str, object] = {}
    quizzes = {}
    for week, topic in enumerate(TOPICS, start=1):
        tasks[topic] = await seed_published_task(session, seeded.named(topic).topic, week_no=week)
        quizzes[topic] = await seed_quiz(
            session,
            world,
            seeded_document=seeded,
            topic_title=topic,
            question_count=MAX_SCORE,
        )

    base = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    for index, student in enumerate(students):
        for topic in TOPICS:
            quiz = quizzes[topic]
            for attempt_index, score in enumerate(_scores_for(topic, index)):
                submitted = base + timedelta(days=index + attempt_index)
                attempt = QuizAttempt(
                    quiz_id=quiz.quiz.id,
                    student_id=student.id,
                    started_at=submitted - timedelta(minutes=30),
                    submitted_at=submitted,
                    score=score,
                    max_score=MAX_SCORE,
                    updated_at=submitted,
                )
                session.add(attempt)
                await session.flush()
                for question_index, question in enumerate(quiz.questions):
                    correct = question_index < score
                    selected = (
                        question.correct_option if correct else (question.correct_option + 1) % 4
                    )
                    session.add(
                        AttemptAnswer(
                            attempt_id=attempt.id,
                            question_id=question.id,
                            selected_option=selected,
                            is_correct=correct,
                            time_spent_ms=5_000,
                            answered_at=submitted,
                        )
                    )

        completed_topics = []
        if index < 9:
            completed_topics.append("Topic A")
        if index < 6:
            completed_topics.append("Topic B")
        if index < 4:
            completed_topics.append("Topic C")
        for topic in completed_topics:
            session.add(
                TaskProgress(
                    task_id=tasks[topic].id,  # type: ignore[attr-defined]
                    student_id=student.id,
                    status=TaskProgressStatus.COMPLETED,
                    completed_at=base + timedelta(days=index),
                    updated_at=base + timedelta(days=index),
                )
            )

    await session.commit()
    return SimpleNamespace(
        students=students,
        seeded=seeded,
        tasks=tasks,
        quizzes=quizzes,
    )


async def test_cohort_kpis_match_hand_computed_fixture(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    await _seed_fixture(session, world)
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    response = await teacher.get(f"/api/v1/teacher/analytics?course_id={world.course_a.id}")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "students": 10,
        "completion_rate": 63.3,
        "pending": 11,
        "completed": 19,
        "avg_score": 64.2,
        "active_today": 4,
    }


async def test_student_rows_exact_numbers_and_sorting(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    fixture = await _seed_fixture(session, world)
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    response = await teacher.get(
        f"/api/v1/teacher/analytics/students?course_id={world.course_a.id}&sort=name"
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["name"] for item in items] == [f"Student {index:02d}" for index in range(10)]

    by_id = {item["id"]: item for item in items}
    first = by_id[str(fixture.students[0].id)]
    assert first["completion_rate"] == 100.0
    assert first["completed"] == 3
    assert first["pending"] == 0
    assert first["days_active"] == 2
    assert first["avg_score"] == 54.2
    assert first["at_risk"] is True
    assert first["weak_topic_count"] == 2
    assert first["last_active_at"] is not None

    middle = by_id[str(fixture.students[5].id)]
    assert middle["completion_rate"] == 66.7
    assert middle["completed"] == 2
    assert middle["pending"] == 1
    assert middle["avg_score"] == 70.8
    assert middle["at_risk"] is False
    assert middle["weak_topic_count"] == 1

    last = by_id[str(fixture.students[9].id)]
    assert last["completion_rate"] == 0.0
    assert last["completed"] == 0
    assert last["pending"] == 3
    assert last["avg_score"] == 70.8
    assert last["weak_topic_count"] == 1

    by_score = await teacher.get(
        f"/api/v1/teacher/analytics/students?course_id={world.course_a.id}&sort=avg_score"
    )
    assert by_score.status_code == 200, by_score.text
    assert by_score.json()["items"][0]["name"] == "Student 07"

    at_risk = await teacher.get(
        f"/api/v1/teacher/analytics/students?course_id={world.course_a.id}&sort=at_risk"
    )
    assert at_risk.status_code == 200, at_risk.text
    flagged = [item for item in at_risk.json()["items"] if item["at_risk"]]
    assert [item["name"] for item in flagged] == [f"Student {index:02d}" for index in range(5)]


async def test_pagination_returns_a_stable_next_cursor(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    await _seed_fixture(session, world)
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    first = await teacher.get(
        f"/api/v1/teacher/analytics/students?course_id={world.course_a.id}&sort=name&limit=4"
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["items"]) == 4
    assert body["next_cursor"]

    second = await teacher.get(
        f"/api/v1/teacher/analytics/students?course_id={world.course_a.id}"
        f"&sort=name&limit=4&cursor={body['next_cursor']}"
    )
    assert second.status_code == 200, second.text
    assert [item["name"] for item in second.json()["items"]] == [
        "Student 04",
        "Student 05",
        "Student 06",
        "Student 07",
    ]


async def test_topic_analytics_heatmap_and_gaps(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    await _seed_fixture(session, world)
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    response = await teacher.get(f"/api/v1/teacher/analytics/topics?course_id={world.course_a.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    by_title = {item["title"]: item for item in body["items"]}

    assert by_title["Topic A"]["release_state"] == "released"
    assert by_title["Topic A"]["completed"] == 9
    assert by_title["Topic A"]["pending"] == 1
    assert by_title["Topic A"]["completion_rate"] == 90.0
    assert by_title["Topic A"]["attempt_count"] == 20
    assert by_title["Topic A"]["avg_score"] == 90.0
    assert by_title["Topic A"]["question_accuracy"] == 90.0

    assert by_title["Topic B"]["completed"] == 6
    assert by_title["Topic B"]["pending"] == 4
    assert by_title["Topic B"]["completion_rate"] == 60.0
    assert by_title["Topic B"]["avg_score"] == 52.5
    assert by_title["Topic B"]["question_accuracy"] == 52.5

    assert by_title["Topic C"]["completed"] == 4
    assert by_title["Topic C"]["pending"] == 6
    assert by_title["Topic C"]["completion_rate"] == 40.0
    assert by_title["Topic C"]["avg_score"] == 50.0
    assert by_title["Topic C"]["question_accuracy"] == 50.0

    topic_gaps = [gap for gap in body["gaps"] if gap["kind"] == "topic"]
    assert [(gap["topic_title"], gap["accuracy"]) for gap in topic_gaps] == [
        ("Topic C", 50.0),
        ("Topic B", 52.5),
        ("Topic A", 90.0),
    ]

    question_gaps = [gap for gap in body["gaps"] if gap["kind"] == "question"]
    assert question_gaps[0]["topic_title"] == "Topic C"
    assert question_gaps[0]["accuracy"] == 0.0
    assert question_gaps[0]["answers"] == 20
    assert question_gaps[0]["question_stem"]
    accuracies = [gap["accuracy"] for gap in question_gaps]
    assert accuracies == sorted(accuracies)


async def test_student_drilldown_tasks_attempts_and_weakest_questions(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    fixture = await _seed_fixture(session, world)
    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    student_id = fixture.students[0].id

    response = await teacher.get(
        f"/api/v1/teacher/analytics/students/{student_id}?course_id={world.course_a.id}"
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["student"]["name"] == "Student 00"
    assert body["summary"] == {
        "completion_rate": 100.0,
        "completed": 3,
        "pending": 0,
        "avg_score": 54.2,
        "days_active": 2,
        "last_active_at": body["summary"]["last_active_at"],
        "at_risk": True,
        "weak_topic_count": 2,
    }
    assert body["summary"]["last_active_at"] is not None

    assert len(body["tasks"]) == 3
    assert {task["status"] for task in body["tasks"]} == {"completed"}
    assert all(task["completed_at"] is not None for task in body["tasks"])

    assert len(body["attempts"]) == 6
    assert {attempt["percentage"] for attempt in body["attempts"]} == {25.0, 50.0, 100.0}
    assert all(attempt["submitted_at"] for attempt in body["attempts"])
    assert all(attempt["duration_seconds"] == 1_800 for attempt in body["attempts"])

    weak = body["weakest_questions"]
    assert len(weak) == 6
    assert {item["topic_title"] for item in weak} == {"Topic B", "Topic C"}
    assert all(item["source_page"] is not None for item in weak)
    assert all(item["source_document"] for item in weak)
    assert all(item["selected_option"] != item["correct_option"] for item in weak)


async def test_course_ownership_and_role_enforcement(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    await _seed_fixture(session, world)

    # Cross-scope resources are 404, matching the rest of the API (no existence leak).
    teacher_b = await client_factory()
    await login(teacher_b, world.teacher_b.email)
    forbidden = await teacher_b.get(f"/api/v1/teacher/analytics?course_id={world.course_a.id}")
    assert forbidden.status_code == 404
    assert forbidden.json()["detail"] == "Course not found"

    student_client = await client_factory()
    await login(student_client, world.student.email)
    denied = await student_client.get(f"/api/v1/teacher/analytics?course_id={world.course_a.id}")
    assert denied.status_code == 403

    admin = await client_factory()
    await login(admin, world.admin.email)
    allowed = await admin.get(f"/api/v1/teacher/analytics?course_id={world.course_a.id}")
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["students"] == 10

    missing_student = await teacher_b.get(
        f"/api/v1/teacher/analytics/students/{world.student.id}?course_id={world.course_b.id}"
    )
    assert missing_student.status_code == 404


async def test_completion_invalidates_cached_cohort(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    fixture = await _seed_fixture(session, world)
    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    url = f"/api/v1/teacher/analytics?course_id={world.course_a.id}"

    before = await teacher.get(url)
    assert before.status_code == 200, before.text
    assert before.json()["completed"] == 19

    # Reading again is served from the 60-second cache even though the fixture
    # changed underneath it.
    task_id = fixture.tasks["Topic C"].id  # type: ignore[attr-defined]
    session.add(
        TaskProgress(
            task_id=task_id,
            student_id=fixture.students[9].id,
            status=TaskProgressStatus.COMPLETED,
            completed_at=datetime.now(UTC),
        )
    )
    await session.commit()
    cached = await teacher.get(url)
    assert cached.json()["completed"] == 19

    # The real completion endpoint invalidates every cached filter for the course.
    student_client = await client_factory()
    await login(student_client, fixture.students[9].email)
    response = await student_client.patch(
        f"/api/v1/student/tasks/{task_id}",
        json={"status": "completed"},
    )
    assert response.status_code == 200, response.text

    after = await teacher.get(url)
    assert after.status_code == 200, after.text
    assert after.json()["completed"] == 20
    assert after.json()["pending"] == 10
    assert after.json()["completion_rate"] == 66.7


async def test_unknown_course_and_invalid_cursor(
    client_factory, world, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    await _seed_fixture(session, world)
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    unknown = await teacher.get(f"/api/v1/teacher/analytics?course_id={uuid.uuid4()}")
    assert unknown.status_code == 404

    invalid = await teacher.get(
        f"/api/v1/teacher/analytics/students?course_id={world.course_a.id}&cursor=not-a-cursor"
    )
    assert invalid.status_code == 422
