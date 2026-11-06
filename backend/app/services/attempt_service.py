"""Student quiz attempts: start/resume, autosave, submit, results, history.

Grading is deterministic and server-side. A student response never contains
``correct_option``, ``explanation``, or ``source_chunk_id`` before submission.
Once an attempt is submitted its answers are immutable: saves are rejected and
a repeated submit returns the stored result instead of recomputing it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.quizzes import (
    AnswerInput,
    AttemptAnswerOut,
    AttemptReviewItem,
    AttemptSaveResult,
    QuizCourseRef,
    StudentAttemptList,
    StudentAttemptResult,
    StudentAttemptState,
    StudentAttemptSummary,
    StudentQuestionOut,
    StudentQuizDetail,
    StudentQuizOut,
)
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.scope import CourseScope
from app.db.models import (
    AttemptAnswer,
    Course,
    Question,
    Quiz,
    QuizAttempt,
    Topic,
)
from app.db.repositories import quizzes as quizzes_repo
from app.db.repositories import topics as topics_repo
from app.services import analytics_service


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _percentage(score: int | None, max_score: int | None) -> int | None:
    if score is None or not max_score:
        return None
    return round(score * 100 / max_score)


def _course_ref(course: Course) -> QuizCourseRef:
    return QuizCourseRef(id=course.id, code=course.code, title=course.title)


def _student_question(question: Question) -> StudentQuestionOut:
    return StudentQuestionOut(
        id=question.id,
        stem=question.stem,
        options=list(question.options),
        order_index=question.order_index,
    )


def _quiz_out(
    quiz: Quiz,
    topic: Topic,
    course: Course,
    *,
    question_count: int,
    stats: quizzes_repo.AttemptStats,
) -> StudentQuizOut:
    return StudentQuizOut(
        id=quiz.id,
        topic_id=quiz.topic_id,
        course=_course_ref(course),
        title=quiz.title,
        question_count=question_count,
        pass_threshold=quiz.pass_threshold,
        attempt_limit=quiz.attempt_limit,
        attempts_used=stats.submitted,
        best_score=stats.best_percentage,
        open_attempt_id=stats.open_attempt_id,
    )


async def _visible_quiz(
    session: AsyncSession,
    *,
    scope: CourseScope,
    quiz_id: uuid.UUID,
) -> tuple[Quiz, Topic, Course]:
    row = await quizzes_repo.get_student_quiz(
        session,
        user_id=scope.user_id,
        scope=scope,
        quiz_id=quiz_id,
    )
    if row is None:
        raise NotFoundError("Quiz not found")
    return row


async def list_student_quizzes(
    session: AsyncSession,
    *,
    scope: CourseScope,
    course_id: uuid.UUID | None = None,
) -> list[StudentQuizOut]:
    if course_id is not None:
        scope.require(course_id)
    rows = await quizzes_repo.list_student_quizzes(
        session,
        user_id=scope.user_id,
        scope=scope,
        course_id=course_id,
    )
    quiz_ids = [quiz.id for quiz, _, _ in rows]
    counts = await quizzes_repo.question_counts(session, quiz_ids)
    stats = await quizzes_repo.attempt_stats(
        session,
        student_id=scope.user_id,
        quiz_ids=quiz_ids,
    )
    items: list[StudentQuizOut] = []
    for quiz, topic, course in rows:
        item = counts.get(quiz.id)
        items.append(
            _quiz_out(
                quiz,
                topic,
                course,
                question_count=item.approved if item else 0,
                stats=stats.get(quiz.id, quizzes_repo.AttemptStats()),
            )
        )
    return items


async def get_student_quiz(
    session: AsyncSession,
    *,
    scope: CourseScope,
    quiz_id: uuid.UUID,
) -> StudentQuizDetail:
    quiz, topic, course = await _visible_quiz(session, scope=scope, quiz_id=quiz_id)
    questions = await quizzes_repo.list_questions(session, quiz.id, approved_only=True)
    stats = (
        await quizzes_repo.attempt_stats(
            session,
            student_id=scope.user_id,
            quiz_ids=[quiz.id],
        )
    ).get(quiz.id, quizzes_repo.AttemptStats())
    return StudentQuizDetail(
        quiz=_quiz_out(
            quiz,
            topic,
            course,
            question_count=len(questions),
            stats=stats,
        ),
        questions=[_student_question(question) for question in questions],
    )


async def _state(
    session: AsyncSession,
    *,
    attempt: QuizAttempt,
    quiz: Quiz,
    topic: Topic,
    course: Course,
    questions: list[Question],
) -> StudentAttemptState:
    answers = await quizzes_repo.list_answers(session, attempt.id)
    stats = (
        await quizzes_repo.attempt_stats(
            session,
            student_id=attempt.student_id,
            quiz_ids=[quiz.id],
        )
    ).get(quiz.id, quizzes_repo.AttemptStats())
    return StudentAttemptState(
        attempt_id=attempt.id,
        quiz=_quiz_out(
            quiz,
            topic,
            course,
            question_count=len(questions),
            stats=stats,
        ),
        started_at=_as_utc(attempt.started_at) or datetime.now(UTC),
        submitted_at=_as_utc(attempt.submitted_at),
        questions=[_student_question(question) for question in questions],
        answers=[
            AttemptAnswerOut(
                question_id=answer.question_id,
                selected_option=answer.selected_option,
                time_spent_ms=answer.time_spent_ms,
            )
            for answer in answers
        ],
    )


async def start_attempt(
    session: AsyncSession,
    *,
    scope: CourseScope,
    quiz_id: uuid.UUID,
) -> StudentAttemptState:
    quiz, topic, course = await _visible_quiz(session, scope=scope, quiz_id=quiz_id)
    questions = await quizzes_repo.list_questions(session, quiz.id, approved_only=True)
    if not questions:
        raise ConflictError("This quiz has no questions yet")

    existing = await quizzes_repo.get_open_attempt(
        session,
        student_id=scope.user_id,
        quiz_id=quiz.id,
    )
    if existing is not None:
        return await _state(
            session,
            attempt=existing,
            quiz=quiz,
            topic=topic,
            course=course,
            questions=questions,
        )

    submitted = await quizzes_repo.count_submitted_attempts(
        session,
        student_id=scope.user_id,
        quiz_id=quiz.id,
    )
    if quiz.attempt_limit > 0 and submitted >= quiz.attempt_limit:
        raise ConflictError("You have reached the attempt limit for this quiz")

    attempt = QuizAttempt(quiz_id=quiz.id, student_id=scope.user_id)
    session.add(attempt)
    try:
        await session.commit()
    except IntegrityError:
        # Another request won the partial-unique race; resume that attempt.
        await session.rollback()
        existing = await quizzes_repo.get_open_attempt(
            session,
            student_id=scope.user_id,
            quiz_id=quiz.id,
        )
        if existing is None:
            raise
        attempt = existing

    return await _state(
        session,
        attempt=attempt,
        quiz=quiz,
        topic=topic,
        course=course,
        questions=questions,
    )


async def get_attempt_state(
    session: AsyncSession,
    *,
    scope: CourseScope,
    attempt_id: uuid.UUID,
) -> StudentAttemptState:
    attempt = await quizzes_repo.get_attempt_for_student(
        session,
        attempt_id=attempt_id,
        student_id=scope.user_id,
    )
    if attempt is None:
        raise NotFoundError("Attempt not found")
    quiz = await quizzes_repo.get(session, attempt.quiz_id)
    topic = await topics_repo.get(session, quiz.topic_id) if quiz is not None else None
    course = await session.get(Course, quiz.course_id) if quiz is not None else None
    if quiz is None or topic is None or course is None:
        raise NotFoundError("Attempt not found")

    if attempt.submitted_at is None:
        # An open attempt must still be visible to the student.
        row = await quizzes_repo.get_student_quiz(
            session,
            user_id=scope.user_id,
            scope=scope,
            quiz_id=quiz.id,
        )
        if row is None:
            raise NotFoundError("Attempt not found")
        questions = await quizzes_repo.list_questions(session, quiz.id, approved_only=True)
    else:
        questions = await quizzes_repo.list_questions(session, quiz.id)

    return await _state(
        session,
        attempt=attempt,
        quiz=quiz,
        topic=topic,
        course=course,
        questions=questions,
    )


def _answer_map(
    questions: list[Question],
    answers: list[AnswerInput],
) -> dict[uuid.UUID, AnswerInput]:
    by_id = {question.id: question for question in questions}
    selected: dict[uuid.UUID, AnswerInput] = {}
    for answer in answers:
        if answer.question_id not in by_id:
            raise ValidationError("An answer refers to a question outside this quiz")
        selected[answer.question_id] = answer
    return selected


async def _apply_answers(
    session: AsyncSession,
    *,
    attempt: QuizAttempt,
    questions: list[Question],
    answers: list[AnswerInput],
) -> int:
    selected = _answer_map(questions, answers)
    now = datetime.now(UTC)
    for question_id, answer in selected.items():
        row = await quizzes_repo.get_answer(
            session,
            attempt_id=attempt.id,
            question_id=question_id,
        )
        if row is None:
            row = AttemptAnswer(
                attempt_id=attempt.id,
                question_id=question_id,
                selected_option=answer.selected_option,
                time_spent_ms=answer.time_spent_ms,
                answered_at=now,
            )
            session.add(row)
        else:
            row.selected_option = answer.selected_option
            if answer.time_spent_ms is not None:
                row.time_spent_ms = answer.time_spent_ms
            row.answered_at = now
    await session.flush()
    return len(selected)


async def save_attempt(
    session: AsyncSession,
    *,
    scope: CourseScope,
    attempt_id: uuid.UUID,
    payload_answers: list[AnswerInput],
) -> AttemptSaveResult:
    attempt = await quizzes_repo.get_attempt_for_update(
        session,
        attempt_id=attempt_id,
        student_id=scope.user_id,
    )
    if attempt is None:
        raise NotFoundError("Attempt not found")
    if attempt.submitted_at is not None:
        raise ConflictError("This attempt has already been submitted")

    quiz, _topic, _course = await _visible_quiz(session, scope=scope, quiz_id=attempt.quiz_id)
    questions = await quizzes_repo.list_questions(session, quiz.id, approved_only=True)
    saved = await _apply_answers(
        session,
        attempt=attempt,
        questions=questions,
        answers=payload_answers,
    )
    answers = await quizzes_repo.list_answers(session, attempt.id)
    await session.commit()
    return AttemptSaveResult(
        saved=saved,
        answered=sum(1 for answer in answers if answer.selected_option is not None),
        total=len(questions),
    )


def _result(
    attempt: QuizAttempt,
    quiz: Quiz,
    questions: list[Question],
    answers: list[AttemptAnswer],
) -> StudentAttemptResult:
    by_question = {answer.question_id: answer for answer in answers}
    review: list[AttemptReviewItem] = []
    correct = 0
    for question in questions:
        answer = by_question.get(question.id)
        selected = answer.selected_option if answer is not None else None
        is_correct = selected is not None and selected == question.correct_option
        if is_correct:
            correct += 1
        review.append(
            AttemptReviewItem(
                question_id=question.id,
                stem=question.stem,
                options=list(question.options),
                selected_option=selected,
                correct_option=question.correct_option,
                is_correct=is_correct,
                explanation=question.explanation,
                source_chunk_id=question.source_chunk_id,
            )
        )

    max_score = len(questions)
    percentage = _percentage(correct, max_score) or 0
    return StudentAttemptResult(
        attempt_id=attempt.id,
        quiz_id=quiz.id,
        quiz_title=quiz.title,
        score=correct,
        max_score=max_score,
        percentage=percentage,
        passed=percentage >= quiz.pass_threshold,
        pass_threshold=quiz.pass_threshold,
        submitted_at=_as_utc(attempt.submitted_at) or datetime.now(UTC),
        review=review,
    )


async def submit_attempt(
    session: AsyncSession,
    *,
    scope: CourseScope,
    attempt_id: uuid.UUID,
    payload_answers: list[AnswerInput],
) -> StudentAttemptResult:
    attempt = await quizzes_repo.get_attempt_for_update(
        session,
        attempt_id=attempt_id,
        student_id=scope.user_id,
    )
    if attempt is None:
        raise NotFoundError("Attempt not found")

    quiz, _topic, _course = await _visible_quiz(session, scope=scope, quiz_id=attempt.quiz_id)
    questions = await quizzes_repo.list_questions(session, quiz.id)
    if not questions:
        raise ConflictError("This quiz has no questions")

    if attempt.submitted_at is not None:
        # Idempotent repeat submit: return the stored result, never regrade.
        answers = await quizzes_repo.list_answers(session, attempt.id)
        return _result(attempt, quiz, questions, answers)

    await _apply_answers(
        session,
        attempt=attempt,
        questions=questions,
        answers=payload_answers,
    )
    answers = await quizzes_repo.list_answers(session, attempt.id)
    by_question = {answer.question_id: answer for answer in answers}
    now = datetime.now(UTC)
    correct = 0
    for question in questions:
        answer = by_question.get(question.id)
        if answer is None:
            answer = AttemptAnswer(
                attempt_id=attempt.id,
                question_id=question.id,
                selected_option=None,
                is_correct=False,
                answered_at=now,
            )
            session.add(answer)
            by_question[question.id] = answer
        answer.is_correct = (
            answer.selected_option is not None and answer.selected_option == question.correct_option
        )
        if answer.is_correct:
            correct += 1

    attempt.score = correct
    attempt.max_score = len(questions)
    attempt.submitted_at = now
    await session.commit()
    await analytics_service.invalidate_course(quiz.course_id)
    return _result(attempt, quiz, questions, list(by_question.values()))


async def get_attempt_result(
    session: AsyncSession,
    *,
    scope: CourseScope,
    attempt_id: uuid.UUID,
) -> StudentAttemptResult:
    attempt = await quizzes_repo.get_attempt_for_student(
        session,
        attempt_id=attempt_id,
        student_id=scope.user_id,
    )
    if attempt is None:
        raise NotFoundError("Attempt not found")
    if attempt.submitted_at is None:
        raise ConflictError("This attempt has not been submitted yet")
    quiz = await quizzes_repo.get(session, attempt.quiz_id)
    if quiz is None:
        raise NotFoundError("Attempt not found")
    questions = await quizzes_repo.list_questions(session, quiz.id)
    answers = await quizzes_repo.list_answers(session, attempt.id)
    return _result(attempt, quiz, questions, answers)


async def list_attempts(
    session: AsyncSession,
    *,
    scope: CourseScope,
    quiz_id: uuid.UUID | None = None,
) -> StudentAttemptList:
    attempts = await quizzes_repo.list_attempts_for_student(
        session,
        student_id=scope.user_id,
        quiz_id=quiz_id,
    )
    items: list[StudentAttemptSummary] = []
    for attempt in attempts:
        quiz = await quizzes_repo.get(session, attempt.quiz_id)
        if quiz is None:
            continue
        course = await session.get(Course, quiz.course_id)
        if course is None:
            continue
        percentage = _percentage(attempt.score, attempt.max_score)
        open_attempt = attempt.submitted_at is None
        items.append(
            StudentAttemptSummary(
                id=attempt.id,
                quiz_id=quiz.id,
                quiz_title=quiz.title,
                course=_course_ref(course),
                started_at=_as_utc(attempt.started_at) or datetime.now(UTC),
                submitted_at=_as_utc(attempt.submitted_at),
                score=attempt.score,
                max_score=attempt.max_score,
                percentage=percentage,
                passed=(percentage is not None and percentage >= quiz.pass_threshold),
                open=open_attempt,
            )
        )
    return StudentAttemptList(items=items, next_cursor=None)
