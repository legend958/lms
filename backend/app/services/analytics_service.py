"""Teacher analytics: cohort KPIs, students, topics, gaps, and drill-down.

All numbers are computed with indexed SQL aggregates on read. The service never
depends on the client for arithmetic and never trusts a client-supplied course
id without checking it against the teacher's ``CourseScope``.

Results are cached for 60 seconds per ``(course_id, filter_hash)``. Release,
task completion, and attempt submission invalidate every cached filter for the
affected course.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Float, and_, case, cast, distinct, func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.analytics import (
    AnalyticsAttemptItem,
    AnalyticsStudentDetail,
    AnalyticsStudentList,
    AnalyticsStudentRef,
    AnalyticsStudentRow,
    AnalyticsStudentSummary,
    AnalyticsTaskHistoryItem,
    AnalyticsTopics,
    AnalyticsWeakQuestion,
    CohortKpis,
    KnowledgeGap,
    TopicAnalytics,
)
from app.core.analytics_cache import AnalyticsCache, get_analytics_cache
from app.core.errors import NotFoundError, ValidationError
from app.core.roles import (
    CourseRole,
    TaskProgressStatus,
    TaskStatus,
    TopicReleaseState,
    UserStatus,
)
from app.core.scope import CourseScope
from app.db.models import (
    AttemptAnswer,
    Chunk,
    Course,
    Document,
    Enrollment,
    Question,
    Quiz,
    QuizAttempt,
    Task,
    TaskProgress,
    Topic,
    User,
)

StudentSort = Literal["completion", "name", "last_active", "avg_score", "days_active", "at_risk"]
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
AT_RISK_TOPIC_THRESHOLD = 60.0
AT_RISK_TOPIC_COUNT = 2


def _percentage(numerator: float | int, denominator: float | int) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) * 100.0 / float(denominator), 1)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _score_percentage(score: int | None, max_score: int | None) -> float | None:
    if score is None or not max_score:
        return None
    return round(score * 100.0 / max_score, 1)


def _average_percentage() -> Any:
    """SQL expression for an attempt's percentage; ignores zero-length quizzes."""
    return cast(QuizAttempt.score, Float) / QuizAttempt.max_score * 100.0


def _correct_flag() -> Any:
    """Portable ``is_correct`` to 1/0 expression (Postgres cannot cast bool to float)."""
    return case((AttemptAnswer.is_correct.is_(True), 1), else_=0)


async def _require_course(
    session: AsyncSession, scope: CourseScope, course_id: uuid.UUID
) -> Course:
    scope.require(course_id)
    course = await session.get(Course, course_id)
    if course is None:
        raise NotFoundError("Course not found")
    return course


def _student_base(course_id: uuid.UUID) -> Any:
    return (
        select(User.id, User.name, User.email, User.last_active_at)
        .select_from(User)
        .join(Enrollment, Enrollment.user_id == User.id)
        .where(
            Enrollment.course_id == course_id,
            Enrollment.course_role == CourseRole.STUDENT,
            User.status == UserStatus.ACTIVE,
        )
    )


async def _student_count(session: AsyncSession, course_id: uuid.UUID) -> int:
    rows = (await session.execute(_student_base(course_id))).all()
    return len({row[0] for row in rows})


def _published_tasks(course_id: uuid.UUID) -> Any:
    return (
        select(Task.id, Task.topic_id, Task.week_no)
        .select_from(Task)
        .join(Topic, Topic.id == Task.topic_id)
        .join(Document, Document.id == Topic.document_id)
        .where(
            Task.course_id == course_id,
            Task.status == TaskStatus.PUBLISHED,
            Topic.release_state == TopicReleaseState.RELEASED,
            Topic.deleted_at.is_(None),
            Document.deleted_at.is_(None),
        )
    )


async def _published_task_count(
    session: AsyncSession, course_id: uuid.UUID, week: int | None
) -> int:
    stmt = _published_tasks(course_id)
    if week is not None:
        stmt = stmt.where(Task.week_no == week)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    return int((await session.execute(count_stmt)).scalar_one())


def _completed_by_student(course_id: uuid.UUID, week: int | None) -> Any:
    stmt = (
        select(
            TaskProgress.student_id,
            func.count(TaskProgress.task_id).label("completed"),
        )
        .select_from(TaskProgress)
        .join(Task, Task.id == TaskProgress.task_id)
        .join(Topic, Topic.id == Task.topic_id)
        .join(Document, Document.id == Topic.document_id)
        .join(
            Enrollment,
            and_(
                Enrollment.user_id == TaskProgress.student_id,
                Enrollment.course_id == course_id,
                Enrollment.course_role == CourseRole.STUDENT,
            ),
        )
        .join(User, and_(User.id == TaskProgress.student_id, User.status == UserStatus.ACTIVE))
        .where(
            Task.course_id == course_id,
            Task.status == TaskStatus.PUBLISHED,
            TaskProgress.status == TaskProgressStatus.COMPLETED,
            Topic.release_state == TopicReleaseState.RELEASED,
            Topic.deleted_at.is_(None),
            Document.deleted_at.is_(None),
        )
        .group_by(TaskProgress.student_id)
    )
    if week is not None:
        stmt = stmt.where(Task.week_no == week)
    return stmt


def _attempts_by_student(course_id: uuid.UUID, week: int | None) -> Any:
    stmt = (
        select(
            QuizAttempt.student_id,
            func.avg(_average_percentage()).label("avg_score"),
            func.count(QuizAttempt.id).label("attempts"),
        )
        .select_from(QuizAttempt)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .join(
            Enrollment,
            and_(
                Enrollment.user_id == QuizAttempt.student_id,
                Enrollment.course_id == course_id,
                Enrollment.course_role == CourseRole.STUDENT,
            ),
        )
        .join(User, and_(User.id == QuizAttempt.student_id, User.status == UserStatus.ACTIVE))
        .where(
            Quiz.course_id == course_id,
            QuizAttempt.submitted_at.is_not(None),
            QuizAttempt.score.is_not(None),
            QuizAttempt.max_score.is_not(None),
            QuizAttempt.max_score > 0,
        )
        .group_by(QuizAttempt.student_id)
    )
    if week is not None:
        stmt = stmt.join(
            Task,
            and_(
                Task.topic_id == Quiz.topic_id,
                Task.course_id == course_id,
                Task.status == TaskStatus.PUBLISHED,
                Task.week_no == week,
            ),
        )
    return stmt


def _days_active_by_student(course_id: uuid.UUID, week: int | None) -> Any:
    progress_activity = (
        select(
            TaskProgress.student_id.label("student_id"),
            func.date(TaskProgress.updated_at).label("activity_day"),
        )
        .select_from(TaskProgress)
        .join(Task, Task.id == TaskProgress.task_id)
        .join(Topic, Topic.id == Task.topic_id)
        .join(Document, Document.id == Topic.document_id)
        .join(
            Enrollment,
            and_(
                Enrollment.user_id == TaskProgress.student_id,
                Enrollment.course_id == course_id,
                Enrollment.course_role == CourseRole.STUDENT,
            ),
        )
        .where(
            Task.course_id == course_id,
            Task.status == TaskStatus.PUBLISHED,
            Topic.release_state == TopicReleaseState.RELEASED,
            Topic.deleted_at.is_(None),
            Document.deleted_at.is_(None),
        )
    )
    attempt_activity = (
        select(
            QuizAttempt.student_id.label("student_id"),
            func.date(QuizAttempt.submitted_at).label("activity_day"),
        )
        .select_from(QuizAttempt)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .join(
            Enrollment,
            and_(
                Enrollment.user_id == QuizAttempt.student_id,
                Enrollment.course_id == course_id,
                Enrollment.course_role == CourseRole.STUDENT,
            ),
        )
        .where(
            Quiz.course_id == course_id,
            QuizAttempt.submitted_at.is_not(None),
        )
    )
    if week is not None:
        progress_activity = progress_activity.where(Task.week_no == week)
        attempt_activity = attempt_activity.join(
            Task,
            and_(
                Task.topic_id == Quiz.topic_id,
                Task.course_id == course_id,
                Task.status == TaskStatus.PUBLISHED,
                Task.week_no == week,
            ),
        )
    activity = union_all(progress_activity, attempt_activity).subquery()
    return select(activity.c.student_id, func.count(distinct(activity.c.activity_day))).group_by(
        activity.c.student_id
    )


def _topic_scores_by_student(course_id: uuid.UUID) -> Any:
    return (
        select(
            QuizAttempt.student_id,
            Quiz.topic_id,
            func.avg(_average_percentage()).label("avg_score"),
        )
        .select_from(QuizAttempt)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .where(
            Quiz.course_id == course_id,
            QuizAttempt.submitted_at.is_not(None),
            QuizAttempt.max_score.is_not(None),
            QuizAttempt.max_score > 0,
        )
        .group_by(QuizAttempt.student_id, Quiz.topic_id)
    )


async def _student_rows(
    session: AsyncSession,
    *,
    course_id: uuid.UUID,
    week: int | None = None,
) -> list[AnalyticsStudentRow]:
    students = (await session.execute(_student_base(course_id))).all()
    if not students:
        return []

    task_count = await _published_task_count(session, course_id, week)
    completed = {
        row[0]: int(row[1])
        for row in (await session.execute(_completed_by_student(course_id, week))).all()
    }
    attempts = {
        row[0]: (float(row[1]), int(row[2]))
        for row in (await session.execute(_attempts_by_student(course_id, week))).all()
        if row[1] is not None
    }
    days_active = {
        row[0]: int(row[1])
        for row in (await session.execute(_days_active_by_student(course_id, week))).all()
    }
    weak_topics: Counter[uuid.UUID] = Counter()
    for student_id, _topic_id, average in (
        await session.execute(_topic_scores_by_student(course_id))
    ).all():
        if average is not None and float(average) < AT_RISK_TOPIC_THRESHOLD:
            weak_topics[student_id] += 1

    rows: list[AnalyticsStudentRow] = []
    for student_id, name, email, last_active_at in students:
        completed_count = completed.get(student_id, 0)
        pending = max(task_count - completed_count, 0)
        average_score, _attempt_count = attempts.get(student_id, (None, 0))
        weak_count = weak_topics.get(student_id, 0)
        rows.append(
            AnalyticsStudentRow(
                id=student_id,
                name=name,
                email=email,
                completion_rate=_percentage(completed_count, task_count),
                completed=completed_count,
                pending=pending,
                last_active_at=_as_utc(last_active_at),
                days_active=days_active.get(student_id, 0),
                avg_score=None if average_score is None else round(average_score, 1),
                at_risk=weak_count >= AT_RISK_TOPIC_COUNT,
                weak_topic_count=weak_count,
            )
        )
    return rows


def _sort_students(rows: list[AnalyticsStudentRow], sort: StudentSort) -> list[AnalyticsStudentRow]:
    if sort == "name":
        return sorted(rows, key=lambda row: (row.name.lower(), str(row.id)))
    if sort == "last_active":
        return sorted(
            rows,
            key=lambda row: (
                row.last_active_at is not None,
                row.last_active_at or datetime.min.replace(tzinfo=UTC),
                row.name.lower(),
            ),
            reverse=True,
        )
    if sort == "avg_score":
        return sorted(
            rows,
            key=lambda row: (row.avg_score is not None, row.avg_score or 0.0),
            reverse=True,
        )
    if sort == "days_active":
        return sorted(rows, key=lambda row: (-row.days_active, row.name.lower()))
    if sort == "at_risk":
        return sorted(
            rows,
            key=lambda row: (not row.at_risk, -row.weak_topic_count, -row.completion_rate),
        )
    return sorted(rows, key=lambda row: (-row.completion_rate, row.name.lower()))


def _encode_cursor(sort: StudentSort, offset: int) -> str:
    payload = json.dumps({"s": sort, "o": offset}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, sort: StudentSort) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
        if payload.get("s") != sort:
            raise ValueError("cursor sort mismatch")
        offset = int(payload["o"])
        if offset < 0:
            raise ValueError("negative offset")
        return offset
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValidationError("Invalid pagination cursor") from exc


async def _compute_cohort(
    session: AsyncSession,
    *,
    course_id: uuid.UUID,
    week: int | None,
) -> CohortKpis:
    students = await _student_count(session, course_id)
    task_count = await _published_task_count(session, course_id, week)
    completed_subquery = _completed_by_student(course_id, week).subquery()
    completed_tasks = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(completed_subquery.c.completed), 0))
            )
        ).scalar_one()
    )
    del completed_subquery

    average_score: float | None = None
    score_stmt = (
        select(func.avg(_average_percentage()))
        .select_from(QuizAttempt)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .where(
            Quiz.course_id == course_id,
            QuizAttempt.submitted_at.is_not(None),
            QuizAttempt.score.is_not(None),
            QuizAttempt.max_score.is_not(None),
            QuizAttempt.max_score > 0,
            QuizAttempt.student_id.in_(
                select(Enrollment.user_id).where(
                    Enrollment.course_id == course_id,
                    Enrollment.course_role == CourseRole.STUDENT,
                )
            ),
        )
    )
    if week is not None:
        score_stmt = score_stmt.join(
            Task,
            and_(
                Task.topic_id == Quiz.topic_id,
                Task.course_id == course_id,
                Task.status == TaskStatus.PUBLISHED,
                Task.week_no == week,
            ),
        )
    average = (await session.execute(score_stmt)).scalar_one_or_none()
    if average is not None:
        average_score = round(float(average), 1)

    today: date = datetime.now(UTC).date()
    day_start = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    active_today = int(
        (
            await session.execute(
                select(func.count(distinct(User.id)))
                .select_from(User)
                .join(Enrollment, Enrollment.user_id == User.id)
                .where(
                    Enrollment.course_id == course_id,
                    Enrollment.course_role == CourseRole.STUDENT,
                    User.status == UserStatus.ACTIVE,
                    User.last_active_at >= day_start,
                    User.last_active_at < day_end,
                )
            )
        ).scalar_one()
    )

    denominator = students * task_count
    return CohortKpis(
        students=students,
        completion_rate=_percentage(completed_tasks, denominator),
        pending=max(denominator - completed_tasks, 0),
        completed=completed_tasks,
        avg_score=average_score,
        active_today=active_today,
    )


async def cohort_kpis(
    session: AsyncSession,
    *,
    scope: CourseScope,
    course_id: uuid.UUID,
    week: int | None = None,
) -> CohortKpis:
    await _require_course(session, scope, course_id)
    cache = get_analytics_cache()
    filter_hash = AnalyticsCache.filter_hash(
        {"endpoint": "kpis", "course_id": course_id, "week": week}
    )
    cached = await cache.get(str(course_id), filter_hash)
    if cached is not None:
        return CohortKpis.model_validate(cached)
    result = await _compute_cohort(session, course_id=course_id, week=week)
    await cache.set(str(course_id), filter_hash, result.model_dump(mode="json"))
    return result


async def student_rows(
    session: AsyncSession,
    *,
    scope: CourseScope,
    course_id: uuid.UUID,
    week: int | None = None,
    sort: StudentSort = "completion",
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> AnalyticsStudentList:
    await _require_course(session, scope, course_id)
    limit = max(1, min(limit, MAX_LIMIT))
    cache = get_analytics_cache()
    filter_hash = AnalyticsCache.filter_hash(
        {
            "endpoint": "students",
            "course_id": course_id,
            "week": week,
            "sort": sort,
            "cursor": cursor,
            "limit": limit,
        }
    )
    cached = await cache.get(str(course_id), filter_hash)
    if cached is not None:
        return AnalyticsStudentList.model_validate(cached)

    rows = _sort_students(
        await _student_rows(session, course_id=course_id, week=week),
        sort,
    )
    offset = _decode_cursor(cursor, sort) if cursor else 0
    page = rows[offset : offset + limit]
    next_offset = offset + len(page)
    next_cursor = _encode_cursor(sort, next_offset) if next_offset < len(rows) else None
    result = AnalyticsStudentList(items=page, next_cursor=next_cursor)
    await cache.set(str(course_id), filter_hash, result.model_dump(mode="json"))
    return result


def _attempts_by_topic(course_id: uuid.UUID) -> Any:
    return (
        select(
            Quiz.topic_id,
            func.count(QuizAttempt.id).label("attempt_count"),
            func.avg(_average_percentage()).label("avg_score"),
        )
        .select_from(Quiz)
        .join(QuizAttempt, QuizAttempt.quiz_id == Quiz.id)
        .where(
            Quiz.course_id == course_id,
            QuizAttempt.submitted_at.is_not(None),
            QuizAttempt.score.is_not(None),
            QuizAttempt.max_score.is_not(None),
            QuizAttempt.max_score > 0,
        )
        .group_by(Quiz.topic_id)
    )


def _question_accuracy_by_topic(course_id: uuid.UUID) -> Any:
    return (
        select(
            Quiz.topic_id,
            func.count(AttemptAnswer.question_id).label("answers"),
            func.sum(_correct_flag()).label("correct"),
        )
        .select_from(AttemptAnswer)
        .join(QuizAttempt, QuizAttempt.id == AttemptAnswer.attempt_id)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .where(Quiz.course_id == course_id, QuizAttempt.submitted_at.is_not(None))
        .group_by(Quiz.topic_id)
    )


def _completed_by_topic(course_id: uuid.UUID) -> Any:
    return (
        select(Task.topic_id, func.count(TaskProgress.task_id))
        .select_from(Task)
        .join(TaskProgress, TaskProgress.task_id == Task.id)
        .join(Topic, Topic.id == Task.topic_id)
        .join(Document, Document.id == Topic.document_id)
        .join(
            Enrollment,
            and_(
                Enrollment.user_id == TaskProgress.student_id,
                Enrollment.course_id == course_id,
                Enrollment.course_role == CourseRole.STUDENT,
            ),
        )
        .join(User, and_(User.id == TaskProgress.student_id, User.status == UserStatus.ACTIVE))
        .where(
            Task.course_id == course_id,
            Task.status == TaskStatus.PUBLISHED,
            TaskProgress.status == TaskProgressStatus.COMPLETED,
            Topic.release_state == TopicReleaseState.RELEASED,
            Topic.deleted_at.is_(None),
            Document.deleted_at.is_(None),
        )
        .group_by(Task.topic_id)
    )


def _question_gaps(course_id: uuid.UUID) -> Any:
    answers = func.count(AttemptAnswer.question_id)
    correct = func.sum(_correct_flag())
    accuracy = cast(correct, Float) / cast(answers, Float)
    return (
        select(
            Question.id,
            Question.stem,
            Topic.id,
            Topic.title,
            answers.label("answers"),
            correct.label("correct"),
        )
        .select_from(Question)
        .join(AttemptAnswer, AttemptAnswer.question_id == Question.id)
        .join(QuizAttempt, QuizAttempt.id == AttemptAnswer.attempt_id)
        .join(Quiz, Quiz.id == Question.quiz_id)
        .join(Topic, Topic.id == Quiz.topic_id)
        .where(Quiz.course_id == course_id, QuizAttempt.submitted_at.is_not(None))
        .group_by(Question.id, Question.stem, Topic.id, Topic.title)
        .having(answers > 0)
        .order_by(accuracy.asc(), answers.desc())
        .limit(10)
    )


async def _compute_topics(
    session: AsyncSession,
    *,
    course_id: uuid.UUID,
) -> AnalyticsTopics:
    students = await _student_count(session, course_id)
    topic_rows = (
        await session.execute(
            select(
                Topic.id,
                Topic.title,
                Topic.document_id,
                Document.filename,
                Topic.release_state,
                Task.id,
            )
            .select_from(Topic)
            .join(Document, Document.id == Topic.document_id)
            .outerjoin(
                Task,
                and_(Task.topic_id == Topic.id, Task.status == TaskStatus.PUBLISHED),
            )
            .where(
                Topic.course_id == course_id,
                Topic.deleted_at.is_(None),
                Document.deleted_at.is_(None),
            )
            .order_by(Document.filename, Topic.order_index)
        )
    ).all()

    completed = {
        row[0]: int(row[1]) for row in (await session.execute(_completed_by_topic(course_id))).all()
    }
    attempts = {
        row[0]: (int(row[1]), float(row[2]) if row[2] is not None else None)
        for row in (await session.execute(_attempts_by_topic(course_id))).all()
    }
    accuracy = {
        row[0]: (int(row[1]), int(row[2] or 0))
        for row in (await session.execute(_question_accuracy_by_topic(course_id))).all()
    }

    items: list[TopicAnalytics] = []
    topic_gaps: list[KnowledgeGap] = []
    for topic_id, title, document_id, filename, release_state, task_id in topic_rows:
        completed_count = completed.get(topic_id, 0)
        attempt_count, average = attempts.get(topic_id, (0, None))
        answer_count, correct_count = accuracy.get(topic_id, (0, 0))
        question_accuracy = _percentage(correct_count, answer_count) if answer_count else None
        pending = max(students - completed_count, 0) if task_id is not None else 0
        items.append(
            TopicAnalytics(
                id=topic_id,
                title=title,
                document_id=document_id,
                document_title=filename,
                release_state=release_state,
                students=students,
                completed=completed_count,
                pending=pending,
                completion_rate=_percentage(completed_count, students),
                attempt_count=attempt_count,
                avg_score=None if average is None else round(average, 1),
                question_accuracy=question_accuracy,
            )
        )
        if question_accuracy is not None:
            topic_gaps.append(
                KnowledgeGap(
                    kind="topic",
                    topic_id=topic_id,
                    topic_title=title,
                    accuracy=question_accuracy,
                    answers=answer_count,
                )
            )

    topic_gaps.sort(key=lambda gap: (gap.accuracy, -gap.answers, gap.topic_title.lower()))
    question_gaps = [
        KnowledgeGap(
            kind="question",
            topic_id=topic_id,
            topic_title=topic_title,
            question_id=question_id,
            question_stem=stem,
            accuracy=_percentage(correct, answers),
            answers=answers,
        )
        for question_id, stem, topic_id, topic_title, answers, correct in (
            await session.execute(_question_gaps(course_id))
        ).all()
    ]
    return AnalyticsTopics(items=items, gaps=[*topic_gaps, *question_gaps])


async def topics(
    session: AsyncSession,
    *,
    scope: CourseScope,
    course_id: uuid.UUID,
) -> AnalyticsTopics:
    await _require_course(session, scope, course_id)
    cache = get_analytics_cache()
    filter_hash = AnalyticsCache.filter_hash({"endpoint": "topics", "course_id": course_id})
    cached = await cache.get(str(course_id), filter_hash)
    if cached is not None:
        return AnalyticsTopics.model_validate(cached)
    result = await _compute_topics(session, course_id=course_id)
    await cache.set(str(course_id), filter_hash, result.model_dump(mode="json"))
    return result


async def _compute_student_detail(
    session: AsyncSession,
    *,
    course_id: uuid.UUID,
    student_id: uuid.UUID,
) -> AnalyticsStudentDetail:
    student = (
        await session.execute(
            select(User)
            .join(Enrollment, Enrollment.user_id == User.id)
            .where(
                User.id == student_id,
                Enrollment.course_id == course_id,
                Enrollment.course_role == CourseRole.STUDENT,
                User.status == UserStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()
    if student is None:
        raise NotFoundError("Student not found")

    rows = await _student_rows(session, course_id=course_id)
    row = next((item for item in rows if item.id == student_id), None)
    if row is None:
        raise NotFoundError("Student not found")

    task_rows = (
        await session.execute(
            select(
                Task.id,
                Task.title,
                Task.week_no,
                Task.due_at,
                TaskProgress.status,
                TaskProgress.completed_at,
            )
            .select_from(Task)
            .outerjoin(
                TaskProgress,
                and_(
                    TaskProgress.task_id == Task.id,
                    TaskProgress.student_id == student_id,
                ),
            )
            .join(Topic, Topic.id == Task.topic_id)
            .join(Document, Document.id == Topic.document_id)
            .where(
                Task.course_id == course_id,
                Task.status == TaskStatus.PUBLISHED,
                Topic.release_state == TopicReleaseState.RELEASED,
                Topic.deleted_at.is_(None),
                Document.deleted_at.is_(None),
            )
            .order_by(Task.week_no.asc(), Task.due_at.asc(), Task.id.asc())
        )
    ).all()

    attempt_rows = (
        await session.execute(
            select(
                QuizAttempt.id,
                QuizAttempt.quiz_id,
                Quiz.title,
                Topic.title,
                QuizAttempt.started_at,
                QuizAttempt.submitted_at,
                QuizAttempt.score,
                QuizAttempt.max_score,
            )
            .select_from(QuizAttempt)
            .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
            .join(Topic, Topic.id == Quiz.topic_id)
            .where(
                Quiz.course_id == course_id,
                QuizAttempt.student_id == student_id,
            )
            .order_by(QuizAttempt.started_at.desc())
        )
    ).all()

    weak_rows = (
        await session.execute(
            select(
                Question.id,
                Quiz.topic_id,
                Topic.title,
                Question.stem,
                AttemptAnswer.selected_option,
                Question.correct_option,
                Question.explanation,
                Document.filename,
                Chunk.page_start,
            )
            .select_from(Question)
            .join(AttemptAnswer, AttemptAnswer.question_id == Question.id)
            .join(QuizAttempt, QuizAttempt.id == AttemptAnswer.attempt_id)
            .join(Quiz, Quiz.id == Question.quiz_id)
            .join(Topic, Topic.id == Quiz.topic_id)
            .outerjoin(Chunk, Chunk.id == Question.source_chunk_id)
            .outerjoin(Document, Document.id == Chunk.document_id)
            .where(
                QuizAttempt.student_id == student_id,
                Quiz.course_id == course_id,
                QuizAttempt.submitted_at.is_not(None),
                AttemptAnswer.is_correct.is_(False),
            )
            .order_by(QuizAttempt.submitted_at.desc())
            .limit(25)
        )
    ).all()

    weakest: list[AnalyticsWeakQuestion] = []
    seen_questions: set[uuid.UUID] = set()
    for (
        question_id,
        topic_id,
        topic_title,
        stem,
        selected_option,
        correct_option,
        explanation,
        source_document,
        source_page,
    ) in weak_rows:
        if question_id in seen_questions:
            continue
        seen_questions.add(question_id)
        weakest.append(
            AnalyticsWeakQuestion(
                question_id=question_id,
                topic_id=topic_id,
                topic_title=topic_title,
                stem=stem,
                selected_option=selected_option,
                correct_option=correct_option,
                explanation=explanation,
                source_document=source_document,
                source_page=source_page,
            )
        )
        if len(weakest) >= 10:
            break

    attempts: list[AnalyticsAttemptItem] = []
    for (
        attempt_id,
        quiz_id,
        quiz_title,
        topic_title,
        started_at,
        submitted_at,
        score,
        max_score,
    ) in attempt_rows:
        started = _as_utc(started_at) or started_at
        submitted = _as_utc(submitted_at)
        duration = None
        if submitted is not None:
            duration = max(int((submitted - started).total_seconds()), 0)
        attempts.append(
            AnalyticsAttemptItem(
                attempt_id=attempt_id,
                quiz_id=quiz_id,
                quiz_title=quiz_title,
                topic_title=topic_title,
                started_at=started,
                submitted_at=submitted,
                score=score,
                max_score=max_score,
                percentage=_score_percentage(score, max_score),
                duration_seconds=duration,
            )
        )

    return AnalyticsStudentDetail(
        student=AnalyticsStudentRef(id=student.id, name=student.name, email=student.email),
        summary=AnalyticsStudentSummary(
            completion_rate=row.completion_rate,
            completed=row.completed,
            pending=row.pending,
            avg_score=row.avg_score,
            days_active=row.days_active,
            last_active_at=row.last_active_at,
            at_risk=row.at_risk,
            weak_topic_count=row.weak_topic_count,
        ),
        tasks=[
            AnalyticsTaskHistoryItem(
                task_id=task_id,
                title=title,
                week_no=week_no,
                due_at=_as_utc(due_at) or due_at,
                status=progress_status or TaskProgressStatus.INCOMPLETE,
                completed_at=_as_utc(completed_at),
            )
            for task_id, title, week_no, due_at, progress_status, completed_at in task_rows
        ],
        attempts=attempts,
        weakest_questions=weakest,
    )


async def student_detail(
    session: AsyncSession,
    *,
    scope: CourseScope,
    course_id: uuid.UUID,
    student_id: uuid.UUID,
) -> AnalyticsStudentDetail:
    await _require_course(session, scope, course_id)
    cache = get_analytics_cache()
    filter_hash = AnalyticsCache.filter_hash(
        {"endpoint": "student", "course_id": course_id, "student_id": student_id}
    )
    cached = await cache.get(str(course_id), filter_hash)
    if cached is not None:
        return AnalyticsStudentDetail.model_validate(cached)
    result = await _compute_student_detail(session, course_id=course_id, student_id=student_id)
    await cache.set(str(course_id), filter_hash, result.model_dump(mode="json"))
    return result


async def invalidate_course(course_id: uuid.UUID) -> None:
    """Invalidate every cached analytics response for a course."""
    await get_analytics_cache().invalidate(str(course_id))
