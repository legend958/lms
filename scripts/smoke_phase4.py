"""End-to-end Phase 4 smoke test against a running Compose stack.

Exercises the real path that SQLite unit tests stub out: Postgres, the ARQ
worker, MiniLM embeddings, Chroma, topic extraction fallback, release fan-out,
and the student task/notification endpoints.

Prereqs (from the repo root)::

    docker compose --env-file .env -f infra/docker-compose.yml up -d --build
    docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.seed

Run::

    backend/.venv/Scripts/python.exe scripts/smoke_phase4.py

Exit code 0 means every check passed.
"""

from __future__ import annotations

import io
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

BASE_URL = os.environ.get("LMS_BASE_URL", "http://localhost:8000")
PASSWORD = "Passw0rd!"
TEACHER_EMAIL = "teacher2@lms.example.com"  # teaches SCI101 and MATH101
STUDENT_EMAIL = "student01@lms.example.com"  # enrolled in both courses
POLL_SECONDS = 3
READY_TIMEOUT_SECONDS = 300

results: list[tuple[bool, str, str]] = []


def check(label: str, passed: bool, detail: str = "") -> None:
    results.append((passed, label, detail))
    marker = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"[{marker}] {label}{suffix}")


def login(email: str) -> httpx.Client:
    # Docker Desktop's host->container proxy occasionally resets a connection
    # mid-session; retrying at the transport layer keeps the smoke run stable.
    transport = httpx.HTTPTransport(retries=5)
    client = httpx.Client(
        base_url=BASE_URL,
        timeout=120.0,
        follow_redirects=True,
        transport=transport,
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    response.raise_for_status()
    csrf = client.cookies.get("lms_csrf")
    if not csrf:
        raise RuntimeError(f"no CSRF cookie after login as {email}")
    client.headers["x-csrf-token"] = csrf
    return client


def make_pdf(title: str, sections: list[tuple[str, str]]) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    def draw_heading(text: str, y: float) -> float:
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(72, y, text)
        return y - 32

    def draw_body(text: str, y: float) -> float:
        pdf.setFont("Helvetica", 11)
        words = text.split()
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if pdf.stringWidth(candidate, "Helvetica", 11) > width - 144:
                pdf.drawString(72, y, line)
                y -= 16
                line = word
            else:
                line = candidate
        if line:
            pdf.drawString(72, y, line)
            y -= 16
        return y - 14

    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawString(72, height - 90, title)
    y = height - 150
    for heading, body in sections:
        if y < 160:
            pdf.showPage()
            y = height - 90
        y = draw_heading(heading, y)
        y = draw_body(body, y)
    pdf.save()
    return buffer.getvalue()


def upload(client: httpx.Client, *, course_id: str, filename: str, data: bytes) -> str:
    response = client.post(
        "/api/v1/documents",
        data={"course_id": course_id},
        files={"file": (filename, data, "application/pdf")},
    )
    check(
        f"upload {filename}",
        response.status_code == 202,
        f"HTTP {response.status_code} {response.text[:200]}",
    )
    response.raise_for_status()
    return str(response.json()["document_id"])


def wait_until_ready(client: httpx.Client, document_id: str) -> dict[str, Any]:
    deadline = time.time() + READY_TIMEOUT_SECONDS
    status: dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(f"/api/v1/documents/{document_id}/status")
        response.raise_for_status()
        status = response.json()
        if status["status"] in {"ready", "failed"}:
            return status
        print(f"    … {status['status']} ({status['stage']}, {status['progress_pct']}%)")
        time.sleep(POLL_SECONDS)
    return status


def first_draft_topic(client: httpx.Client, document_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/documents/{document_id}/topics")
    response.raise_for_status()
    topics = response.json()
    drafts = [topic for topic in topics if topic["release_state"] == "draft"]
    if not drafts:
        raise RuntimeError(f"document {document_id} produced no draft topics: {topics}")
    return drafts[0]


def release(
    client: httpx.Client,
    *,
    topic_id: str,
    week_no: int,
    due_at: datetime,
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/topics/{topic_id}/release",
        json={"week_no": week_no, "due_at": due_at.isoformat(), "generate_quiz": False},
    )
    check(
        f"release topic week {week_no}",
        response.status_code == 200,
        f"HTTP {response.status_code} {response.text[:200]}",
    )
    response.raise_for_status()
    return response.json()


def tasks_by_id(client: httpx.Client) -> dict[str, dict[str, Any]]:
    response = client.get("/api/v1/student/tasks")
    response.raise_for_status()
    return {item["id"]: item for item in response.json()["items"]}


def main() -> int:
    print(f"Smoke testing {BASE_URL}\n")

    teacher = login(TEACHER_EMAIL)
    student = login(STUDENT_EMAIL)

    memberships = teacher.get("/api/v1/auth/me").json()["courses"]
    by_code = {course["code"]: course["id"] for course in memberships}
    course_a, course_b = by_code.get("SCI101"), by_code.get("MATH101")
    check("teacher teaches both seeded courses", bool(course_a and course_b), str(by_code))
    if not course_a or not course_b:
        return 1

    stamp = datetime.now(UTC).strftime("%H%M%S")
    science = make_pdf(
        "Chemical Reactions",
        [
            (
                "Reactants and Products",
                "A chemical reaction rearranges atoms. Reactants are the starting substances "
                "and products are the substances formed. Mass is conserved in every closed "
                "system, so the number of atoms of each element is unchanged. " * 3,
            ),
            (
                "Balancing Equations",
                "Coefficients scale whole formulas so that each element balances on both "
                "sides. Never change subscripts to balance an equation, because that changes "
                "the substance itself. " * 3,
            ),
        ],
    )
    maths = make_pdf(
        "Foundations of Algebra",
        [
            (
                "Variables and Expressions",
                "A variable is a symbol standing for an unknown value. An expression combines "
                "variables, numbers, and operations but has no equals sign. " * 3,
            ),
            (
                "Solving Linear Equations",
                "Apply the same operation to both sides to isolate the variable. Check the "
                "solution by substituting it back into the original equation. " * 3,
            ),
        ],
    )

    science_doc = upload(
        teacher, course_id=course_a, filename=f"smoke-science-{stamp}.pdf", data=science
    )
    maths_doc = upload(teacher, course_id=course_b, filename=f"smoke-maths-{stamp}.pdf", data=maths)

    print("\nWaiting for the worker to ingest both PDFs…")
    science_status = wait_until_ready(teacher, science_doc)
    maths_status = wait_until_ready(teacher, maths_doc)
    check(
        "science document ingested",
        science_status.get("status") == "ready" and science_status.get("chunk_count", 0) > 0,
        f"{science_status.get('status')} chunks={science_status.get('chunk_count')}",
    )
    check(
        "maths document ingested",
        maths_status.get("status") == "ready" and maths_status.get("chunk_count", 0) > 0,
        f"{maths_status.get('status')} chunks={maths_status.get('chunk_count')}",
    )

    science_topic = first_draft_topic(teacher, science_doc)
    maths_topic = first_draft_topic(teacher, maths_doc)
    check(
        "topic extraction produced titles",
        bool(science_topic["title"] and maths_topic["title"]),
        f"{science_topic['title']!r} / {maths_topic['title']!r}",
    )
    sources = {
        document_id: teacher.get(f"/api/v1/documents/{document_id}").json()["topics_source"]
        for document_id in (science_doc, maths_doc)
    }
    check(
        "topic extraction used the Groq LLM",
        set(sources.values()) == {"llm"},
        str(sources),
    )

    now = datetime.now(UTC)
    release_a = release(
        teacher, topic_id=str(science_topic["id"]), week_no=1, due_at=now + timedelta(days=7)
    )
    release_b = release(
        teacher, topic_id=str(maths_topic["id"]), week_no=2, due_at=now - timedelta(days=2)
    )
    check(
        "Chroma metadata synced on release",
        release_a["chroma_synced"] and release_b["chroma_synced"],
        f"science={release_a['chroma_synced']} maths={release_b['chroma_synced']}",
    )

    print("\nChecking the student experience…")
    tasks = tasks_by_id(student)
    new_task_ids = {release_a["task_id"], release_b["task_id"]}
    check(
        "student sees both released tasks",
        new_task_ids <= tasks.keys(),
        f"{len(tasks)} tasks, new={new_task_ids}",
    )
    check(
        "tasks span both courses",
        {tasks[task_id]["course"]["code"] for task_id in new_task_ids} == {"SCI101", "MATH101"},
        str([tasks[task_id]["course"]["code"] for task_id in new_task_ids]),
    )
    maths_task = tasks.get(release_b["task_id"], {})
    science_task = tasks.get(release_a["task_id"], {})
    check(
        "past-due task is overdue", maths_task.get("overdue") is True, str(maths_task.get("due_at"))
    )
    check(
        "future task is not overdue",
        science_task.get("overdue") is False,
        str(science_task.get("due_at")),
    )

    forbidden = {"release_state", "released_by", "released_at", "vector_id", "chunk_id"}
    leaked = forbidden & {key for task in tasks.values() for key in task}
    check("student task payload is release-safe", not leaked, str(leaked))

    first = student.patch(
        f"/api/v1/student/tasks/{science_task['id']}", json={"status": "completed"}
    )
    check("complete a task", first.status_code == 200, f"HTTP {first.status_code}")
    completed_at = first.json()["completed_at"]
    second = student.patch(
        f"/api/v1/student/tasks/{science_task['id']}", json={"status": "completed"}
    )
    check(
        "completion is idempotent",
        second.status_code == 200 and second.json()["completed_at"] == completed_at,
        f"{completed_at} vs {second.json().get('completed_at')}",
    )

    # Completion changed a row, so recompute expectations from fresh state.
    tasks = tasks_by_id(student)
    dashboard = student.get("/api/v1/student/dashboard").json()
    expected_counts = {
        "pending": sum(1 for task in tasks.values() if task["status"] != "completed"),
        "completed": sum(1 for task in tasks.values() if task["status"] == "completed"),
        "overdue": sum(
            1 for task in tasks.values() if task["overdue"] and task["status"] != "completed"
        ),
    }
    check(
        "dashboard counts match the task list",
        dashboard["counts"] == expected_counts,
        f"{dashboard['counts']} vs {expected_counts}",
    )

    inbox = student.get("/api/v1/notifications").json()
    new_notifications = [
        item for item in inbox["items"] if item["payload"].get("task_id") in new_task_ids
    ]
    check(
        "release fan-out reached the student",
        len(new_notifications) == 2,
        f"{len(new_notifications)} new notifications",
    )
    check(
        "notification payload carries the task",
        {item["payload"]["task_id"] for item in new_notifications} == new_task_ids,
        str([item["payload"]["title"] for item in new_notifications]),
    )

    unread_before = inbox["unread_count"]
    notification_id = new_notifications[0]["id"]
    marked = student.post(f"/api/v1/notifications/{notification_id}/read")
    after = student.get("/api/v1/notifications").json()
    check(
        "mark read updates the unread count",
        marked.status_code == 204 and after["unread_count"] == unread_before - 1,
        f"HTTP {marked.status_code} unread={unread_before}->{after['unread_count']}",
    )

    print("\nChecking unrelease / re-release history…")
    unreleased = teacher.post(f"/api/v1/topics/{science_topic['id']}/unrelease")
    check(
        "unrelease hides vectors",
        unreleased.status_code == 200 and unreleased.json()["chroma_synced"] is True,
        f"HTTP {unreleased.status_code} {unreleased.text[:120]}",
    )
    hidden = tasks_by_id(student)
    check(
        "unreleased task disappears",
        release_a["task_id"] not in hidden and release_b["task_id"] in hidden,
        str(list(hidden)),
    )
    blocked = student.patch(
        f"/api/v1/student/tasks/{science_task['id']}", json={"status": "completed"}
    )
    check(
        "unreleased task cannot be completed",
        blocked.status_code == 404,
        f"HTTP {blocked.status_code}",
    )

    release(
        teacher,
        topic_id=str(science_topic["id"]),
        week_no=1,
        due_at=now + timedelta(days=7),
    )
    restored = tasks_by_id(student).get(release_a["task_id"], {})
    check(
        "completion survives unrelease/re-release",
        restored.get("status") == "completed" and restored.get("completed_at") is not None,
        f"status={restored.get('status')} completed_at={restored.get('completed_at')}",
    )
    inbox_after = student.get("/api/v1/notifications").json()
    refanout = [
        item for item in inbox_after["items"] if item["payload"].get("task_id") in new_task_ids
    ]
    check(
        "unrelease/re-release is a new transition and notifies again",
        len(refanout) == 3,
        f"{len(refanout)} notifications for the new tasks",
    )

    repeat = teacher.post(
        f"/api/v1/topics/{science_topic['id']}/release",
        json={
            "week_no": 1,
            "due_at": (now + timedelta(days=7)).isoformat(),
            "generate_quiz": False,
        },
    )
    after_repeat = student.get("/api/v1/notifications").json()
    repeated = [
        item for item in after_repeat["items"] if item["payload"].get("task_id") in new_task_ids
    ]
    check(
        "releasing an already-released topic does not fan out again",
        repeat.status_code == 200 and len(repeated) == 3,
        f"HTTP {repeat.status_code}, {len(repeated)} notifications",
    )

    failed = [label for passed, label, _ in results if not passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("Failed checks:")
        for label in failed:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except httpx.HTTPStatusError as exc:
        print(f"\nHTTP error: {exc.response.status_code} {exc.response.text[:500]}")
        sys.exit(1)
