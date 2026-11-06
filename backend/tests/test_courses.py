from __future__ import annotations

from app.db.models import Enrollment
from app.db.session import get_sessionmaker
from tests.conftest import login


async def test_course_visibility_follows_enrollment(client_factory, world) -> None:
    student = await client_factory()
    await login(student, world.student.email)
    response = await student.get("/api/v1/courses")
    assert response.status_code == 200
    codes = {course["code"] for course in response.json()}
    assert codes == {"SCI101"}

    hidden = await student.get(f"/api/v1/courses/{world.course_b.id}")
    assert hidden.status_code == 404


async def test_teacher_sees_taught_course(client_factory, world) -> None:
    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    response = await teacher.get("/api/v1/courses")
    assert response.status_code == 200
    assert {course["code"] for course in response.json()} == {"SCI101"}


async def test_admin_sees_all_courses(client_factory, world) -> None:
    admin = await client_factory()
    await login(admin, world.admin.email)
    response = await admin.get("/api/v1/courses")
    assert response.status_code == 200
    assert {course["code"] for course in response.json()} == {"SCI101", "MATH101"}


async def test_duplicate_course_code_conflicts(client_factory, world) -> None:
    admin = await client_factory()
    await login(admin, world.admin.email)
    response = await admin.post("/api/v1/courses", json={"code": "SCI101", "title": "Duplicate"})
    assert response.status_code == 409


async def test_admin_enrolls_student_and_visibility_updates(client_factory, world) -> None:
    admin = await client_factory()
    await login(admin, world.admin.email)

    response = await admin.post(
        f"/api/v1/courses/{world.course_b.id}/enrollments",
        json={"user_ids": [str(world.student.id)], "course_role": "student"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["added"] == 1

    student = await client_factory()
    await login(student, world.student.email)
    courses = await student.get("/api/v1/courses")
    assert {course["code"] for course in courses.json()} == {"SCI101", "MATH101"}


async def test_teacher_can_read_enrollments_for_own_course(client_factory, world) -> None:
    teacher = await client_factory()
    await login(teacher, world.teacher.email)

    response = await teacher.get(f"/api/v1/courses/{world.course_a.id}/enrollments")
    assert response.status_code == 200
    emails = {row["email"] for row in response.json()}
    assert "student@example.com" in emails
    assert "teacher@example.com" in emails


async def test_admin_can_remove_enrollment(client_factory, world) -> None:
    admin = await client_factory()
    await login(admin, world.admin.email)

    response = await admin.delete(
        f"/api/v1/courses/{world.course_a.id}/enrollments/{world.student.id}"
    )
    assert response.status_code == 204

    async with get_sessionmaker()() as session:
        remaining = await session.get(
            Enrollment, {"course_id": world.course_a.id, "user_id": world.student.id}
        )
    assert remaining is None

    student = await client_factory()
    await login(student, world.student.email)
    assert (await student.get("/api/v1/courses")).json() == []


async def test_student_cannot_modify_courses(client_factory, world) -> None:
    student = await client_factory()
    await login(student, world.student.email)
    response = await student.patch(f"/api/v1/courses/{world.course_a.id}", json={"title": "Hacked"})
    assert response.status_code == 403


async def test_archived_course_disappears_from_scope(client_factory, world) -> None:
    admin = await client_factory()
    await login(admin, world.admin.email)
    archived = await admin.delete(f"/api/v1/courses/{world.course_b.id}")
    assert archived.status_code == 204

    student = await client_factory()
    await login(student, world.student.email)
    # Course B was never visible to this student; ensure course A remains.
    codes = {course["code"] for course in (await student.get("/api/v1/courses")).json()}
    assert codes == {"SCI101"}


async def test_archived_course_hidden_from_enrolled_members(client_factory, world) -> None:
    admin = await client_factory()
    await login(admin, world.admin.email)
    assert (await admin.delete(f"/api/v1/courses/{world.course_a.id}")).status_code == 204

    student = await client_factory()
    await login(student, world.student.email)
    assert (await student.get("/api/v1/courses")).json() == []
    assert (await student.get(f"/api/v1/courses/{world.course_a.id}")).status_code == 404
    me = (await student.get("/api/v1/auth/me")).json()
    assert me["courses"] == []

    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    assert (await teacher.get("/api/v1/courses")).json() == []


async def test_teacher_student_enrollment_does_not_grant_teaching_access(
    client_factory, world
) -> None:
    admin = await client_factory()
    await login(admin, world.admin.email)
    enrolled = await admin.post(
        f"/api/v1/courses/{world.course_b.id}/enrollments",
        json={"user_ids": [str(world.teacher.id)], "course_role": "student"},
    )
    assert enrolled.status_code == 200, enrolled.text

    teacher = await client_factory()
    await login(teacher, world.teacher.email)
    codes = {course["code"] for course in (await teacher.get("/api/v1/courses")).json()}
    assert codes == {"SCI101"}

    forbidden = await teacher.post(
        "/api/v1/announcements",
        json={
            "title": "Should not publish",
            "body": "Teacher is only a student here",
            "course_id": str(world.course_b.id),
        },
    )
    assert forbidden.status_code == 404
