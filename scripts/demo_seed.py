"""Seed a clean demo dataset through the public HTTP API.

Usage (stack must be running and ``python -m app.seed`` already applied):

    python scripts/demo_seed.py
    python scripts/demo_seed.py --api http://localhost:8000

What it produces, all through real teacher/student journeys:

* three NCERT science chapters ingested into SCI101 (``hesc101``–``hesc103``);
* the first topic of the first two chapters released (two tasks, two quizzes);
* SCI101 / chapter three left entirely unreleased as the leak-gate example;
* five announcements (the three from ``app.seed`` plus two demo posts);
* both quizzes approved and published, then simulated attempts for three
  students with different accuracy profiles so analytics and heatmaps render.

The script is idempotent: documents are matched by filename, released topics by
release state, quizzes by topic, attempts by (student, quiz). Re-running it only
fills in what is missing. It first waits for the API's background embedder
warmup to finish (the first start of a cold stack), because requests can hang
while that model loads on a small host.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

TEACHER = {"email": "teacher1@lms.example.com", "password": "Passw0rd!"}
STUDENTS = [
    {"email": "student01@lms.example.com", "password": "Passw0rd!", "accuracy": 0.9},
    {"email": "student03@lms.example.com", "password": "Passw0rd!", "accuracy": 0.6},
    {"email": "student05@lms.example.com", "password": "Passw0rd!", "accuracy": 0.3},
]
DEFAULT_PDFS = ["hesc101.pdf", "hesc102.pdf", "hesc103.pdf"]
EXTRA_ANNOUNCEMENTS = [
    (
        "Week 1 lab groups",
        "Lab groups for week 1 are posted on the noticeboard. Bring your safety "
        "goggles and a printed copy of the practical sheet.",
    ),
    (
        "Reading list update",
        "The chapter on cell structure has been added to the reading list. "
        "Finish the first ten pages before Thursday's class.",
    ),
]
POLL_SECONDS = 3
INGEST_TIMEOUT = 20 * 60
QUIZ_TIMEOUT = 5 * 60
# Extra attempts for idempotent GETs that lose their keep-alive connection.
GET_RETRIES = 4
# How long to wait for the API's background embedder warmup before seeding.
STARTUP_TIMEOUT = 12 * 60


class DemoError(RuntimeError):
    """A demo step failed in a way the operator must see."""


class Api:
    """Tiny cookie-aware JSON client with CSRF support."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _csrf(self) -> str | None:
        for cookie in self.jar:
            if cookie.name == "lms_csrf":
                return cookie.value
        return None

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
        timeout: float = 180,
        retries: int | None = None,
    ) -> Any:
        url = f"{self.base}{path}"
        headers: dict[str, str] = {"Accept": "application/json"}
        payload: bytes | None = data
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif content_type is not None:
            headers["Content-Type"] = content_type
        if method not in {"GET", "HEAD", "OPTIONS"}:
            csrf = self._csrf()
            if csrf:
                headers["X-CSRF-Token"] = csrf

        request = urllib.request.Request(url, data=payload, headers=headers, method=method)

        # Only idempotent requests are retried. The compose port proxy can drop
        # a keep-alive connection while the API is busy embedding a document
        # (Windows reports WinError 10054); a blind retry of a POST could
        # duplicate an upload, so mutations fail loudly instead.
        idempotent = method in {"GET", "HEAD", "OPTIONS"}
        attempts = retries if retries is not None else (GET_RETRIES if idempotent else 1)
        for attempt in range(1, attempts + 1):
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    raw = response.read()
                    if response.status == 204 or not raw:
                        return None
                    return json.loads(raw)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")
                raise DemoError(f"{method} {path} -> {exc.code}: {detail}") from exc
            except (
                urllib.error.URLError,
                ConnectionResetError,
                TimeoutError,
                OSError,
            ) as exc:
                if attempt == attempts:
                    reason = getattr(exc, "reason", exc)
                    raise DemoError(f"{method} {path} failed: {reason}") from exc
                delay = 2**attempt
                log(
                    f"  {method} {path} interrupted ({exc}); "
                    f"retrying in {delay}s [{attempt}/{attempts - 1}]"
                )
                time.sleep(delay)
        raise AssertionError("unreachable")

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, body: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.request("POST", path, body=body, **kwargs)

    def patch(self, path: str, body: dict[str, Any]) -> Any:
        return self.request("PATCH", path, body=body)

    def upload(self, path: str, *, course_id: str, filename: str, content: bytes) -> Any:
        boundary = uuid.uuid4().hex
        mime = mimetypes.guess_type(filename)[0] or "application/pdf"
        parts = [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="course_id"\r\n\r\n',
            course_id.encode(),
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            (f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n').encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        return self.request(
            "POST",
            path,
            data=b"".join(parts),
            content_type=f"multipart/form-data; boundary={boundary}",
        )


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def login(api: Api, account: dict[str, Any], expected_role: str) -> dict[str, Any]:
    for attempt in range(3):
        try:
            result = api.post(
                "/api/v1/auth/login",
                {"email": account["email"], "password": account["password"]},
            )
            break
        except DemoError as exc:
            # The API rate-limits repeated logins from one IP. A demo operator
            # re-running this script should wait rather than fail.
            if "-> 429" not in str(exc) or attempt == 2:
                raise
            log("  login rate limited; waiting 30s")
            time.sleep(30)
    user = result["user"]
    if user["role"] != expected_role:
        raise DemoError(f"{account['email']} is a {user['role']}, expected {expected_role}")
    return user


def find_course(api: Api, code: str) -> dict[str, Any]:
    for course in api.get("/api/v1/courses"):
        if course["code"] == code:
            return course
    raise DemoError(f"course {code} not found; run `python -m app.seed` first")


def wait_for_api(api: Api) -> None:
    """Wait until the API answers promptly after startup.

    ``lifespan`` warms the local embedding model in a background thread. On a
    small host that load can hold the GIL for long stretches, so requests hang
    until it finishes and Docker Desktop's port proxy resets them after ~30 s.
    Seeding inside that window produces spurious connection errors, so wait for
    three consecutive sub-3-second ``/readyz`` responses before starting.
    Timeouts are attempted once here; the outer loop does the waiting.
    """
    deadline = time.monotonic() + STARTUP_TIMEOUT
    fast = 0
    while time.monotonic() < deadline:
        started = time.monotonic()
        try:
            api.get("/readyz", timeout=5, retries=1)
        except DemoError:
            fast = 0
        else:
            elapsed = time.monotonic() - started
            fast = fast + 1 if elapsed < 3 else 0
            if fast >= 3:
                return
        time.sleep(POLL_SECONDS)
    raise DemoError(f"API did not become responsive within {STARTUP_TIMEOUT}s")


def wait_for_ingest(api: Api, document_id: str, filename: str) -> None:
    deadline = time.monotonic() + INGEST_TIMEOUT
    last = None
    while time.monotonic() < deadline:
        status = api.get(f"/api/v1/documents/{document_id}/status")
        snapshot = (status["status"], status["stage"], status["progress_pct"])
        if snapshot != last:
            log(f"  {filename}: {status['status']} / {status['stage']} ({status['progress_pct']}%)")
            last = snapshot
        if status["status"] == "ready":
            return
        if status["status"] == "failed":
            raise DemoError(
                f"ingestion of {filename} failed: "
                f"{status.get('error_code')} {status.get('error_detail')}"
            )
        time.sleep(POLL_SECONDS)
    raise DemoError(f"ingestion of {filename} did not finish in {INGEST_TIMEOUT}s")


def ensure_documents(api: Api, course_id: str, pdfs: list[Path]) -> list[dict[str, Any]]:
    existing = {row["filename"]: row for row in api.get(f"/api/v1/documents?course_id={course_id}")}
    documents: list[dict[str, Any]] = []
    for pdf in pdfs:
        row = existing.get(pdf.name)
        if row is None:
            log(f"uploading {pdf.name} ({pdf.stat().st_size // 1024} KiB)")
            uploaded = api.upload(
                "/api/v1/documents",
                course_id=course_id,
                filename=pdf.name,
                content=pdf.read_bytes(),
            )
            document_id = uploaded["document_id"]
            row = {"id": document_id, "filename": pdf.name, "status": uploaded["status"]}
        else:
            log(f"reusing existing document {pdf.name} ({row['status']})")
        wait_for_ingest(api, row["id"], pdf.name)
        documents.append(row)
    return documents


def ensure_announcements(api: Api, course_id: str) -> int:
    current = {row["title"] for row in api.get("/api/v1/announcements")}
    for title, body in EXTRA_ANNOUNCEMENTS:
        if title in current:
            continue
        api.post(
            "/api/v1/announcements",
            {"title": title, "body": body, "course_id": course_id},
        )
        log(f"added announcement: {title}")
    return len(api.get("/api/v1/announcements"))


def topics_for(api: Api, document_id: str) -> list[dict[str, Any]]:
    return api.get(f"/api/v1/documents/{document_id}/topics")


def quizzes_for_course(api: Api, course_id: str) -> list[dict[str, Any]]:
    return api.get(f"/api/v1/teacher/quizzes?course_id={course_id}")["items"]


def ensure_release(
    api: Api,
    *,
    topic: dict[str, Any],
    week_no: int,
    due_at: datetime,
    course_quizzes: list[dict[str, Any]],
) -> dict[str, Any]:
    if topic["release_state"] == "released":
        log(f"  topic already released: {topic['title']}")
        quiz = next((q for q in course_quizzes if q["topic_id"] == topic["id"]), None)
        if quiz is None:
            log("    no quiz found; creating one")
            created = api.post(
                f"/api/v1/teacher/topics/{topic['id']}/quiz", {"auto_approve": False}
            )
            return wait_for_quiz(api, created["id"])
        if quiz["generation_status"] == "failed":
            log(f"    quiz {quiz['id'][:8]} failed earlier; regenerating")
            api.post(f"/api/v1/teacher/quizzes/{quiz['id']}/regenerate")
        return wait_for_quiz(api, quiz["id"])

    log(f"  releasing: {topic['title']} (week {week_no})")
    result = api.post(
        f"/api/v1/topics/{topic['id']}/release",
        {
            "week_no": week_no,
            "due_at": due_at.isoformat(),
            "generate_quiz": True,
        },
    )
    quiz_id = result.get("quiz_id")
    if quiz_id is None:
        created = api.post(f"/api/v1/teacher/topics/{topic['id']}/quiz", {"auto_approve": False})
        quiz_id = created["id"]
    return wait_for_quiz(api, quiz_id)


def wait_for_quiz(api: Api, quiz_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + QUIZ_TIMEOUT
    last = None
    while time.monotonic() < deadline:
        quiz = api.get(f"/api/v1/teacher/quizzes/{quiz_id}")
        snapshot = (quiz["generation_status"], quiz["question_count"])
        if snapshot != last:
            log(f"  quiz {quiz['id'][:8]}: {snapshot[0]} ({snapshot[1]} questions)")
            last = snapshot
        if quiz["generation_status"] == "failed":
            raise DemoError(
                f"quiz generation failed: {quiz.get('generation_error') or 'unknown error'}"
            )
        if quiz["generation_status"] == "ready" and quiz["question_count"] > 0:
            if quiz["status"] != "published":
                quiz = api.post(f"/api/v1/teacher/quizzes/{quiz_id}/approve")
                log(f"  published quiz: {quiz['title']} ({quiz['question_count']} questions)")
            return quiz
        time.sleep(POLL_SECONDS)
    raise DemoError(f"quiz {quiz_id} was not generated in {QUIZ_TIMEOUT}s")


def ensure_attempts(
    api: Api,
    *,
    quiz: dict[str, Any],
    student: dict[str, Any],
    correct_options: list[int],
) -> int:
    existing = api.get(f"/api/v1/student/attempts?quiz_id={quiz['id']}")["items"]
    if any(item["submitted_at"] for item in existing):
        return 0

    attempt = api.post(f"/api/v1/student/quizzes/{quiz['id']}/attempts")
    share = float(student["accuracy"])
    cutoff = max(1, round(len(correct_options) * share))
    answers = []
    for index, question in enumerate(attempt["questions"]):
        correct = correct_options[index] if index < len(correct_options) else 0
        selected = correct if index < cutoff else (correct + 1) % 4
        answers.append(
            {
                "question_id": question["id"],
                "selected_option": selected,
                "time_spent_ms": 20_000 + index * 4_000,
            }
        )
    api.request(
        "POST",
        f"/api/v1/student/attempts/{attempt['attempt_id']}/submit",
        body={"answers": answers},
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--course", default="SCI101", help="course code to seed")
    parser.add_argument(
        "--pdfs",
        nargs="+",
        default=DEFAULT_PDFS,
        help="PDF filenames relative to the repository root",
    )
    args = parser.parse_args()

    pdfs = [ROOT / name for name in args.pdfs]
    missing = [str(pdf) for pdf in pdfs if not pdf.exists()]
    if missing:
        raise DemoError(f"missing PDFs: {', '.join(missing)}")

    started = time.monotonic()
    teacher_api = Api(args.api)

    log("waiting for the API to finish its startup warmup")
    wait_for_api(teacher_api)
    log(f"API responsive after {time.monotonic() - started:.0f}s")

    student_apis = {student["email"]: Api(args.api) for student in STUDENTS}

    log(f"signing in as {TEACHER['email']}")
    login(teacher_api, TEACHER, "teacher")
    for student in STUDENTS:
        login(student_apis[student["email"]], student, "student")
    course = find_course(teacher_api, args.course)
    log(f"course {course['code']} = {course['id']}")

    documents = ensure_documents(teacher_api, course["id"], pdfs)
    announcements = ensure_announcements(teacher_api, course["id"])

    quizzes: list[dict[str, Any]] = []
    course_quizzes = quizzes_for_course(teacher_api, course["id"])
    now = datetime.now(UTC)
    for index, document in enumerate(documents):
        topics = topics_for(teacher_api, document["id"])
        if not topics:
            raise DemoError(f"{document['filename']} produced no topics")
        if index < 2:
            quiz = ensure_release(
                teacher_api,
                topic=topics[0],
                week_no=index + 1,
                due_at=now + timedelta(days=7 * (index + 1)),
                course_quizzes=course_quizzes,
            )
            quizzes.append(quiz)
            course_quizzes = quizzes_for_course(teacher_api, course["id"])
        else:
            drafts = sum(1 for topic in topics if topic["release_state"] == "draft")
            log(
                f"  leaving {document['filename']} unreleased "
                f"({drafts} draft topics) as the leak-gate example"
            )

    attempts = 0
    for quiz in quizzes:
        detail = teacher_api.get(f"/api/v1/teacher/quizzes/{quiz['id']}")
        correct = [question["correct_option"] for question in detail["questions"]]
        for student in STUDENTS:
            attempts += ensure_attempts(
                student_apis[student["email"]],
                quiz=quiz,
                student=student,
                correct_options=correct,
            )
            log(f"  attempt: {student['email']} on {quiz['title']!r}")

    elapsed = time.monotonic() - started
    log("")
    log("Demo data ready")
    log(f"  course:         {course['code']} - {course['title']}")
    log(f"  documents:      {len(documents)} ingested")
    log(f"  released:       {len(quizzes)} topics / {len(quizzes)} quizzes")
    log(f"  unreleased:     {documents[-1]['filename']} (all topics draft)")
    log(f"  announcements:  {announcements}")
    log(f"  attempts:       {attempts} new simulated submissions")
    log(f"  elapsed:        {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DemoError as exc:
        print(f"\nDemo seed failed: {exc}", file=sys.stderr)
        sys.exit(1)
