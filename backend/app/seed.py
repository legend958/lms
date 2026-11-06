"""Idempotent development seed data.

Run after migrations:
    python -m app.seed
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.roles import CourseRole, Role, UserStatus
from app.core.security import hash_password
from app.db.models import Announcement, Course, User
from app.db.repositories import courses as courses_repo
from app.db.repositories import users as users_repo
from app.db.session import dispose_engine, get_sessionmaker, init_engine

PASSWORD = "Passw0rd!"


async def _ensure_user(
    session: AsyncSession, *, name: str, email: str, role: Role, password: str
) -> tuple[User, bool]:
    existing = await users_repo.get_by_email(session, email)
    if existing is not None:
        return existing, False
    user = User(
        name=name,
        email=email,
        password_hash=hash_password(password),
        role=role,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()
    return user, True


async def _ensure_course(
    session: AsyncSession, *, code: str, title: str, admin: User
) -> tuple[Course, bool]:
    existing = await courses_repo.get_by_code(session, code)
    if existing is not None:
        return existing, False
    course = Course(code=code, title=title, term="2026-T1", created_by=admin.id)
    session.add(course)
    await session.flush()
    return course, True


async def _ensure_enrollment(
    session: AsyncSession,
    course_id: uuid.UUID,
    user_id: uuid.UUID,
    course_role: CourseRole,
) -> bool:
    existing = await courses_repo.get_enrollment(session, course_id, user_id)
    if existing is not None:
        return False
    await courses_repo.enroll(session, course_id, user_id, course_role)
    return True


async def seed() -> None:
    settings = get_settings()
    init_engine()
    created_users = 0
    created_courses = 0

    async with get_sessionmaker()() as session:
        admin, created = await _ensure_user(
            session,
            name="System Administrator",
            email="admin@lms.example.com",
            role=Role.ADMIN,
            password=settings.seed_admin_password,
        )
        created_users += int(created)

        teachers: list[User] = []
        for index in range(1, 4):
            teacher, created = await _ensure_user(
                session,
                name=f"Teacher {index}",
                email=f"teacher{index}@lms.example.com",
                role=Role.TEACHER,
                password=PASSWORD,
            )
            teachers.append(teacher)
            created_users += int(created)

        students: list[User] = []
        for index in range(1, 31):
            student, created = await _ensure_user(
                session,
                name=f"Student {index:02d}",
                email=f"student{index:02d}@lms.example.com",
                role=Role.STUDENT,
                password=PASSWORD,
            )
            students.append(student)
            created_users += int(created)

        course_a, created = await _ensure_course(
            session, code="SCI101", title="Science — Chemical Reactions", admin=admin
        )
        created_courses += int(created)
        course_b, created = await _ensure_course(
            session, code="MATH101", title="Mathematics — Foundations", admin=admin
        )
        created_courses += int(created)

        for teacher in teachers[:2]:
            await _ensure_enrollment(session, course_a.id, teacher.id, CourseRole.TEACHER)
        for teacher in teachers[1:]:
            await _ensure_enrollment(session, course_b.id, teacher.id, CourseRole.TEACHER)

        for index, student in enumerate(students):
            course = course_a if index % 2 == 0 else course_b
            await _ensure_enrollment(session, course.id, student.id, CourseRole.STUDENT)
            # Every fifth student is enrolled in both courses.
            if index % 5 == 0:
                other = course_b if course is course_a else course_a
                await _ensure_enrollment(session, other.id, student.id, CourseRole.STUDENT)

        now = datetime.now(UTC)
        announcements = [
            (
                "Welcome to the new term",
                "All course materials and weekly tasks will appear here. "
                "Check the announcements feed daily.",
                None,
                admin,
                now - timedelta(hours=2),
            ),
            (
                "Science lab safety",
                "Read the safety guidelines before the first practical session.",
                course_a,
                teachers[0],
                now - timedelta(hours=1),
            ),
            (
                "Problem set schedule",
                "Problem sets are released every Monday and are due the following Sunday.",
                course_b,
                teachers[1],
                now - timedelta(minutes=30),
            ),
        ]
        for title, body, target_course, author, publish_at in announcements:
            found = await session.execute(
                select(Announcement.id).where(Announcement.title == title)
            )
            if found.scalar_one_or_none() is None:
                session.add(
                    Announcement(
                        title=title,
                        body=body,
                        course_id=target_course.id if target_course else None,
                        author_id=author.id,
                        publish_at=publish_at,
                    )
                )

        await session.commit()

        print("Seed complete")
        print(f"  users created: {created_users}")
        print(f"  courses created: {created_courses}")
        print("")
        print("Credentials")
        print(f"  admin    admin@lms.example.com / {settings.seed_admin_password}")
        print(f"  teacher  teacher1@lms.example.com / {PASSWORD}")
        print(f"  student  student01@lms.example.com / {PASSWORD}")
        print("")
        print("Course IDs")
        print(f"  SCI101  {course_a.id}")
        print(f"  MATH101 {course_b.id}")

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(seed())
