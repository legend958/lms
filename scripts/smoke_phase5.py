"""End-to-end Phase 5 smoke test against a running Compose stack with real Groq.

This exercises the path that SQLite unit tests stub out: PDF ingestion, real
topic extraction, real grounded MCQ generation through Groq, teacher review and
publishing, the student attempt lifecycle, deterministic grading, attempt
limits, immutability, and the release/unrelease visibility gate.

Prereqs (from the repo root)::

    docker compose --env-file .env -f infra/docker-compose.yml up -d
    docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.seed

Run::

    backend/.venv/Scripts/python.exe scripts/smoke_phase5.py

Set ``SMOKE_CLEANUP=1`` to unrelease the topic and delete the uploaded document
at the end. By default the released topic, quiz, and attempt history are left in
place so a human can inspect them in the web UI. Exit code 0 means every check
passed.
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

if hasattr(sys.stdout, "reconfigure"):
    # Windows consoles default to cp1252; model output may contain CO₂ etc.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("LMS_BASE_URL", "http://localhost:8000")
PASSWORD = "Passw0rd!"
TEACHER_EMAIL = "teacher2@lms.example.com"  # teaches SCI101 and MATH101
STUDENT_EMAIL = "student01@lms.example.com"  # enrolled in both courses
POLL_SECONDS = 3
READY_TIMEOUT_SECONDS = 300
GENERATION_TIMEOUT_SECONDS = 600
CLEANUP = os.environ.get("SMOKE_CLEANUP", "").strip().lower() in {"1", "true", "yes"}

FORBIDDEN_STUDENT_KEYS = {"correct_option", "explanation", "source_chunk_id"}

results: list[tuple[bool, str, str]] = []


def check(label: str, passed: bool, detail: str = "") -> None:
    results.append((passed, label, detail))
    marker = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"[{marker}] {label}{suffix}")


def note(message: str) -> None:
    print(f"    {message}")


def leaked_keys(payload: Any) -> set[str]:
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in FORBIDDEN_STUDENT_KEYS:
                    found.add(key)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return found


def login(email: str) -> httpx.Client:
    # Docker Desktop's host->container proxy occasionally resets a connection
    # mid-session; retrying at the transport layer keeps the smoke run stable.
    transport = httpx.HTTPTransport(retries=5)
    client = httpx.Client(
        base_url=BASE_URL,
        timeout=180.0,
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
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawString(72, y, text)
        return y - 28

    def draw_body(text: str, y: float) -> float:
        pdf.setFont("Helvetica", 11)
        words = text.split()
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if pdf.stringWidth(candidate, "Helvetica", 11) > width - 144:
                pdf.drawString(72, y, line)
                y -= 15
                line = word
                if y < 90:
                    pdf.showPage()
                    pdf.setFont("Helvetica", 11)
                    y = height - 80
            else:
                line = candidate
        if line:
            pdf.drawString(72, y, line)
            y -= 15
        return y - 16

    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(72, height - 80, title)
    y = height - 130
    for heading, body in sections:
        if y < 160:
            pdf.showPage()
            y = height - 80
        y = draw_heading(heading, y)
        y = draw_body(body, y)
    pdf.save()
    return buffer.getvalue()


def photosynthesis_pdf() -> bytes:
    """A single-theme, information-dense source document.

    The smoke run needs enough grounded material for at least eight distinct
    questions, so every section contributes concrete facts rather than filler.
    """
    sections = [
        (
            "Overview of Photosynthesis",
            "Photosynthesis is the process by which photoautotrophs convert light energy into "
            "chemical energy stored in carbohydrate molecules. In plants, algae, and cyanobacteria "
            "the overall balanced equation is six carbon dioxide molecules plus six water molecules "
            "plus light energy, producing one glucose molecule and six oxygen molecules. The process "
            "occurs in two coordinated stages. The light-dependent reactions take place in the "
            "thylakoid membranes and capture light energy to make ATP and NADPH while releasing "
            "oxygen from water. The light-independent reactions, also called the Calvin cycle, take "
            "place in the stroma and use ATP and NADPH to fix carbon dioxide into sugar. Oxygen is a "
            "by-product of splitting water, not of carbon dioxide fixation, which is why the oxygen "
            "released by a plant comes from water rather than from the carbon dioxide it absorbs.",
        ),
        (
            "Chloroplast Structure",
            "Chloroplasts are double-membrane organelles found in the cytoplasm of plant cells. The "
            "smooth outer membrane surrounds a folded inner membrane, and the space between them is "
            "the intermembrane space. The inner membrane encloses the stroma, a fluid-rich region "
            "that contains enzymes of the Calvin cycle, ribosomes, and circular chloroplast DNA. "
            "Inside the stroma sits a third membrane system called the thylakoid membrane, which is "
            "folded into flattened sacs. Stacks of thylakoids are called grana, and the aqueous space "
            "inside a thylakoid is the lumen. The thylakoid membrane holds photosystems, electron "
            "transport chains, and ATP synthase. Chloroplasts are thought to descend from "
            "endosymbiotic cyanobacteria, which explains their own DNA and ribosomes. The stroma and "
            "the thylakoid lumen have different pH values during active photosynthesis, and this "
            "proton gradient is essential for ATP production.",
        ),
        (
            "Pigments and Light Absorption",
            "Chlorophyll a and chlorophyll b are the primary photosynthetic pigments, while "
            "carotenoids such as beta-carotene and xanthophylls act as accessory pigments. Pigments "
            "absorb photons most strongly in the blue-violet and red regions of the visible "
            "spectrum and reflect green light, which is why leaves look green. An absorption "
            "spectrum plots how much light a pigment absorbs at each wavelength, whereas an action "
            "spectrum plots the rate of photosynthesis at each wavelength. The two spectra closely "
            "match, providing evidence that absorbed light drives photosynthesis. Accessory "
            "pigments broaden the range of wavelengths that can be harvested and also help protect "
            "the photosystems from excess light damage. In the thylakoid membrane, pigments are "
            "organized into antenna complexes around a reaction center. Energy absorbed by antenna "
            "pigments is transferred by resonance energy transfer to the reaction center, where a "
            "special chlorophyll molecule donates an excited electron to an electron acceptor.",
        ),
        (
            "Light-Dependent Reactions",
            "The light-dependent reactions begin at photosystem II, where light excites electrons in "
            "the reaction center. A water-splitting complex replaces those electrons by oxidizing "
            "water, producing protons and molecular oxygen. The electrons travel through a series of "
            "carriers, including plastoquinone, the cytochrome b6f complex, and plastocyanin, before "
            "reaching photosystem I. Photosystem I absorbs another photon and re-excites the "
            "electrons, which are ultimately transferred to NADP+ to form NADPH. As electrons move "
            "through the chain, protons are pumped from the stroma into the thylakoid lumen, "
            "creating an electrochemical gradient. ATP synthase uses the flow of protons back into "
            "the stroma to phosphorylate ADP into ATP. This coupling of electron transport to ATP "
            "synthesis is called photophosphorylation. In cyclic photophosphorylation only "
            "photosystem I operates, electrons cycle back through the chain, and ATP is produced "
            "without NADPH or oxygen release.",
        ),
        (
            "The Calvin Cycle",
            "The Calvin cycle uses the ATP and NADPH made by the light reactions to convert carbon "
            "dioxide into glyceraldehyde-3-phosphate, a three-carbon sugar precursor. The cycle has "
            "three phases: carbon fixation, reduction, and regeneration of the starting material. "
            "In fixation, the enzyme ribulose-1,5-bisphosphate carboxylase/oxygenase, commonly called "
            "RuBisCO, attaches carbon dioxide to ribulose-1,5-bisphosphate, or RuBP, a five-carbon "
            "compound. The unstable six-carbon intermediate immediately splits into two molecules of "
            "3-phosphoglycerate. In reduction, each 3-phosphoglycerate is phosphorylated by ATP and "
            "then reduced by NADPH to glyceraldehyde-3-phosphate. In regeneration, most of the "
            "glyceraldehyde-3-phosphate is rearranged using ATP to rebuild RuBP so the cycle can "
            "continue. For every three turns of the Calvin cycle, three carbon dioxide molecules are "
            "fixed and one net glyceraldehyde-3-phosphate exits as the product used to build glucose "
            "and other carbohydrates. The Calvin cycle does not directly require light, but it "
            "depends on the ATP and NADPH produced by the light reactions.",
        ),
        (
            "Photorespiration and C4 Photosynthesis",
            "RuBisCO can bind oxygen as well as carbon dioxide. When oxygen binds, the enzyme "
            "catalyzes photorespiration, a wasteful process that consumes ATP and releases carbon "
            "dioxide without producing sugar. Photorespiration becomes more likely when stomata "
            "close to conserve water on hot, dry days, because carbon dioxide inside the leaf falls "
            "while oxygen rises. C3 plants, including wheat, rice, and soybeans, fix carbon directly "
            "through the Calvin cycle and are especially vulnerable to photorespiration. C4 plants, "
            "such as maize, sugarcane, and sorghum, reduce the problem by separating initial carbon "
            "fixation from the Calvin cycle in space. In mesophyll cells, the enzyme PEP carboxylase "
            "attaches carbon dioxide to phosphoenolpyruvate to form a four-carbon acid. This acid is "
            "transported to bundle-sheath cells, where it releases carbon dioxide near RuBisCO. The "
            "concentration of carbon dioxide in bundle-sheath cells suppresses photorespiration and "
            "makes C4 photosynthesis efficient in hot, bright environments.",
        ),
        (
            "CAM Plants and Limiting Factors",
            "Crassulacean acid metabolism, or CAM, separates carbon fixation from the Calvin cycle "
            "in time rather than in space. CAM plants such as cacti, pineapple, and agave open their "
            "stomata at night, when temperatures are lower and humidity is higher. At night they fix "
            "carbon dioxide into organic acids, usually malate, which are stored in the vacuole. "
            "During the day the stomata close to conserve water, and the stored acids release carbon "
            "dioxide for the Calvin cycle while the light reactions supply ATP and NADPH. The rate "
            "of photosynthesis is influenced by light intensity, carbon dioxide concentration, "
            "temperature, and water availability. The principle of the limiting factor states that "
            "the rate is constrained by whichever necessary factor is in shortest supply. Increasing "
            "light intensity raises the rate only until another factor, such as carbon dioxide or "
            "temperature, becomes limiting. At very high light intensities, protective mechanisms "
            "such as non-photochemical quenching dissipate excess energy and limit damage to the "
            "photosystems.",
        ),
        (
            "Integrated Summary",
            "Photosynthesis couples the light-driven production of ATP and NADPH to carbon fixation. "
            "The light reactions occur in the thylakoid membranes, split water, release oxygen, and "
            "build a proton gradient that powers ATP synthase. The Calvin cycle occurs in the "
            "stroma, uses RuBisCO to fix carbon dioxide, and consumes ATP and NADPH to produce "
            "glyceraldehyde-3-phosphate. Chlorophyll and carotenoid pigments broaden light capture, "
            "while photosystem II and photosystem I cooperate in noncyclic electron flow. C4 and CAM "
            "plants use spatial and temporal adaptations, respectively, to concentrate carbon "
            "dioxide around RuBisCO and limit photorespiration. The rate of the whole process is "
            "controlled by the availability of light, carbon dioxide, water, and suitable "
            "temperatures, and no single factor can raise the rate once another factor becomes "
            "limiting.",
        ),
    ]
    return make_pdf("Photosynthesis: From Light to Sugar", sections)


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
        note(f"… {status['status']} ({status['stage']}, {status['progress_pct']}%)")
        time.sleep(POLL_SECONDS)
    return status


def wait_for_quiz(client: httpx.Client, quiz_id: str) -> dict[str, Any]:
    deadline = time.time() + GENERATION_TIMEOUT_SECONDS
    detail: dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(f"/api/v1/teacher/quizzes/{quiz_id}")
        response.raise_for_status()
        detail = response.json()
        state = detail.get("generation_status")
        if state in {"ready", "failed"}:
            return detail
        note(f"… quiz generation {state}")
        time.sleep(POLL_SECONDS)
    return detail


def field_check(label: str, actual: Any, expected: Any) -> None:
    check(label, actual == expected, f"{actual!r} != {expected!r}")


def main() -> int:
    print(f"Smoke testing Phase 5 at {BASE_URL}\n")

    teacher = login(TEACHER_EMAIL)
    student = login(STUDENT_EMAIL)

    memberships = teacher.get("/api/v1/auth/me").json()["courses"]
    by_code = {course["code"]: course["id"] for course in memberships}
    course_id = by_code.get("SCI101")
    check("teacher teaches the seeded science course", bool(course_id), str(by_code))
    if not course_id:
        return 1

    stamp = datetime.now(UTC).strftime("%H%M%S")
    document_id = upload(
        teacher,
        course_id=course_id,
        filename=f"smoke-phase5-photosynthesis-{stamp}.pdf",
        data=photosynthesis_pdf(),
    )

    print("\nWaiting for the worker to ingest the PDF…")
    status = wait_until_ready(teacher, document_id)
    check(
        "document ingested with chunks",
        status.get("status") == "ready" and status.get("chunk_count", 0) > 0,
        f"{status.get('status')} chunks={status.get('chunk_count')}",
    )
    if status.get("status") != "ready":
        note(f"status payload: {status}")
        return 1

    topics_response = teacher.get(f"/api/v1/documents/{document_id}/topics")
    topics_response.raise_for_status()
    topics = topics_response.json()
    check("topic extraction produced topics", bool(topics), str(topics)[:200])
    if not topics:
        return 1

    topic = max(topics, key=lambda item: item["chunk_count"])
    check(
        "selected topic has source chunks",
        topic["chunk_count"] > 0 and topic["release_state"] == "draft",
        f"title={topic['title']!r} chunks={topic['chunk_count']} topics={len(topics)}",
    )
    note(f"Using topic {topic['title']!r} ({topic['chunk_count']} chunks)")

    now = datetime.now(UTC)
    release = teacher.post(
        f"/api/v1/topics/{topic['id']}/release",
        json={
            "week_no": 1,
            "due_at": (now + timedelta(days=7)).isoformat(),
            "generate_quiz": True,
        },
    )
    check(
        "release queues quiz generation",
        release.status_code == 200
        and bool(release.json().get("quiz_id"))
        and release.json().get("quiz_queued") is True,
        f"HTTP {release.status_code} {release.text[:200]}",
    )
    release.raise_for_status()
    release_payload = release.json()
    quiz_id = release_payload.get("quiz_id")
    if not quiz_id:
        return 1

    print("\nWaiting for real Groq quiz generation…")
    quiz = wait_for_quiz(teacher, str(quiz_id))
    check(
        "quiz generation finished",
        quiz.get("generation_status") == "ready",
        f"status={quiz.get('generation_status')} error={quiz.get('generation_error')}",
    )
    if quiz.get("generation_status") != "ready":
        return 1

    questions = quiz.get("questions") or []
    check(
        "generated at least eight grounded questions",
        len(questions) >= 8,
        f"{len(questions)} questions",
    )
    check(
        "quiz starts as a teacher-review draft",
        quiz.get("status") == "draft" and quiz.get("needs_review") is True,
        f"status={quiz.get('status')} needs_review={quiz.get('needs_review')}",
    )
    check(
        "auto-approve is off by default",
        quiz.get("auto_approve") is False and quiz.get("approved_count") == 0,
        f"auto_approve={quiz.get('auto_approve')} approved={quiz.get('approved_count')}",
    )

    preview = teacher.get(f"/api/v1/topics/{topic['id']}/preview")
    preview.raise_for_status()
    chunk_ids = {str(chunk["id"]) for chunk in preview.json()["chunks"]}
    check(
        "quiz question sources belong to the released topic",
        {str(question["source_chunk_id"]) for question in questions} <= chunk_ids,
        f"{len(questions)} sources, {len(chunk_ids)} topic chunks",
    )
    structurally_valid = all(
        len(question["options"]) == 4
        and len({option.casefold() for option in question["options"]}) == 4
        and 0 <= question["correct_option"] <= 3
        and 10 <= len(question["stem"]) <= 500
        and bool(question["explanation"])
        for question in questions
    )
    check("every question has four distinct options and a valid key", structurally_valid)

    print("\nChecking that a draft quiz is invisible to students…")
    student_list = student.get("/api/v1/student/quizzes")
    student_list.raise_for_status()
    check(
        "draft quiz is absent from the student list",
        str(quiz_id) not in {item["id"] for item in student_list.json()},
        str([item["id"] for item in student_list.json()]),
    )
    hidden_detail = student.get(f"/api/v1/student/quizzes/{quiz_id}")
    check(
        "draft quiz detail returns 404",
        hidden_detail.status_code == 404,
        f"HTTP {hidden_detail.status_code}",
    )
    hidden_start = student.post(f"/api/v1/student/quizzes/{quiz_id}/attempts")
    check(
        "draft quiz cannot be started",
        hidden_start.status_code == 404,
        f"HTTP {hidden_start.status_code}",
    )

    print("\nChecking the teacher review workflow…")
    first_question = questions[0]
    approved_one = teacher.patch(
        f"/api/v1/teacher/questions/{first_question['id']}",
        json={"approved": True},
    )
    check(
        "a draft question can be marked approved",
        approved_one.status_code == 200 and approved_one.json()["approved"] is True,
        f"HTTP {approved_one.status_code} approved={approved_one.json().get('approved')}",
    )
    edited = teacher.patch(
        f"/api/v1/teacher/questions/{first_question['id']}",
        json={
            "explanation": (
                f"{first_question['explanation']} This explanation was reviewed by the teacher."
            )[:1000]
        },
    )
    check(
        "editing a question resets its approval",
        edited.status_code == 200 and edited.json()["approved"] is False,
        f"HTTP {edited.status_code} approved={edited.json().get('approved')}",
    )

    approve = teacher.post(f"/api/v1/teacher/quizzes/{quiz_id}/approve")
    check(
        "approving publishes the quiz",
        approve.status_code == 200
        and approve.json()["status"] == "published"
        and approve.json()["needs_review"] is False,
        f"HTTP {approve.status_code} {approve.text[:200]}",
    )
    approve.raise_for_status()
    quiz = approve.json()
    questions = quiz["questions"]
    correct_by_id = {question["id"]: question["correct_option"] for question in questions}
    total_questions = len(questions)
    check(
        "approval marks every question approved",
        quiz["approved_count"] == total_questions,
        f"{quiz['approved_count']}/{quiz['question_count']}",
    )

    print("\nChecking the student-facing payload…")
    student_list = student.get("/api/v1/student/quizzes")
    student_list.raise_for_status()
    entry = next(
        (item for item in student_list.json() if item["id"] == str(quiz_id)),
        None,
    )
    check("published quiz appears for the student", entry is not None, str(entry))
    check(
        "student quiz list has no answer key",
        not leaked_keys(student_list.json()),
        str(leaked_keys(student_list.json())),
    )

    student_detail = student.get(f"/api/v1/student/quizzes/{quiz_id}")
    student_detail.raise_for_status()
    detail_payload = student_detail.json()
    check(
        "student detail has every approved question",
        len(detail_payload["questions"]) == total_questions,
        f"{len(detail_payload['questions'])}/{total_questions}",
    )
    check(
        "student detail has no answer key",
        not leaked_keys(detail_payload),
        str(leaked_keys(detail_payload)),
    )

    print("\nChecking start, autosave, submit, and grading…")
    start_one = student.post(f"/api/v1/student/quizzes/{quiz_id}/attempts")
    check("student can start the quiz", start_one.status_code == 200, f"HTTP {start_one.status_code}")
    start_one.raise_for_status()
    attempt_id = start_one.json()["attempt_id"]

    start_two = student.post(f"/api/v1/student/quizzes/{quiz_id}/attempts")
    check(
        "starting again resumes the same open attempt",
        start_two.status_code == 200 and start_two.json()["attempt_id"] == attempt_id,
        f"HTTP {start_two.status_code} {start_two.json().get('attempt_id')}",
    )
    check(
        "start payload has no answer key",
        not leaked_keys(start_two.json()),
        str(leaked_keys(start_two.json())),
    )

    resumed = student.get(f"/api/v1/student/attempts/{attempt_id}")
    resumed.raise_for_status()
    resumed_payload = resumed.json()
    check(
        "the open attempt is resumable with no answers yet",
        resumed_payload["submitted_at"] is None and not resumed_payload["answers"],
        f"submitted_at={resumed_payload['submitted_at']} answers={resumed_payload['answers']}",
    )

    # Autosave one correct answer, then submit a different subset. The submit
    # must merge with the autosaved row instead of replacing the whole attempt.
    seeded = {
        "question_id": questions[0]["id"],
        "selected_option": correct_by_id[questions[0]["id"]],
    }
    autosave = student.patch(
        f"/api/v1/student/attempts/{attempt_id}",
        json={"answers": [seeded]},
    )
    check(
        "autosave stores one answer",
        autosave.status_code == 200
        and autosave.json() == {"saved": 1, "answered": 1, "total": total_questions},
        f"HTTP {autosave.status_code} {autosave.text[:200]}",
    )

    # Submit answers for questions 1..N-2, deliberately leaving the final
    # question unanswered. Odd indices are correct, even indices are wrong.
    submitted_answers: list[dict[str, Any]] = []
    expected: dict[str, int | None] = {
        question["id"]: None for question in questions
    }
    expected[questions[0]["id"]] = seeded["selected_option"]
    for index in range(1, total_questions - 1):
        question = questions[index]
        correct = correct_by_id[question["id"]]
        selected = correct if index % 2 == 1 else (correct + 1) % 4
        expected[question["id"]] = selected
        submitted_answers.append(
            {"question_id": question["id"], "selected_option": selected, "time_spent_ms": 2500}
        )

    expected_correct = sum(
        1
        for question in questions
        if expected[question["id"]] is not None
        and expected[question["id"]] == correct_by_id[question["id"]]
    )
    expected_percentage = round(expected_correct * 100 / total_questions)
    pass_threshold = quiz["pass_threshold"]
    expected_passed = expected_percentage >= pass_threshold

    submit = student.post(
        f"/api/v1/student/attempts/{attempt_id}/submit",
        json={"answers": submitted_answers},
    )
    check("student can submit the attempt", submit.status_code == 200, f"HTTP {submit.status_code}")
    submit.raise_for_status()
    result = submit.json()

    check(
        "server score matches the hand-computed answer set",
        result["score"] == expected_correct,
        f"{result['score']} != {expected_correct}",
    )
    field_check("server max score matches the question count", result["max_score"], total_questions)
    check(
        "server percentage matches the hand-computed score",
        result["percentage"] == expected_percentage,
        f"{result['percentage']} != {expected_percentage}",
    )
    check(
        "pass flag matches the pass threshold",
        result["passed"] is expected_passed,
        f"passed={result['passed']} percentage={result['percentage']} threshold={pass_threshold}",
    )
    field_check("review contains every question", len(result["review"]), total_questions)

    review_matches = True
    review_detail = ""
    for question, item in zip(questions, result["review"], strict=True):
        selected = expected[question["id"]]
        correct = correct_by_id[question["id"]]
        want_correct = selected is not None and selected == correct
        if (
            item["question_id"] != question["id"]
            or item["selected_option"] != selected
            or item["correct_option"] != correct
            or item["is_correct"] is not want_correct
            or not item["explanation"]
            or item["source_chunk_id"] != question["source_chunk_id"]
        ):
            review_matches = False
            review_detail = f"question {question['id']}: {item}"
            break
    check("review flags match the known answer set", review_matches, review_detail)

    last_review = result["review"][-1]
    check(
        "the unanswered question is recorded as skipped",
        last_review["selected_option"] is None and last_review["is_correct"] is False,
        str(last_review),
    )

    submitted_state = student.get(f"/api/v1/student/attempts/{attempt_id}")
    submitted_state.raise_for_status()
    state_payload = submitted_state.json()
    stored_answers = {answer["question_id"]: answer for answer in state_payload["answers"]}
    check(
        "submission persists a row for every question",
        len(stored_answers) == total_questions,
        f"{len(stored_answers)}/{total_questions}",
    )
    check(
        "the unanswered question is stored with a null selection",
        stored_answers[questions[-1]["id"]]["selected_option"] is None,
        str(stored_answers.get(questions[-1]["id"])),
    )
    check(
        "submitted attempt state has no answer key",
        not leaked_keys(state_payload),
        str(leaked_keys(state_payload)),
    )

    print("\nChecking post-submit immutability and limits…")
    late_save = student.patch(
        f"/api/v1/student/attempts/{attempt_id}",
        json={
            "answers": [
                {
                    "question_id": questions[0]["id"],
                    "selected_option": (correct_by_id[questions[0]["id"]] + 1) % 4,
                }
            ]
        },
    )
    check(
        "answers cannot be saved after submission",
        late_save.status_code == 409,
        f"HTTP {late_save.status_code} {late_save.text[:160]}",
    )

    repeat_submit = student.post(
        f"/api/v1/student/attempts/{attempt_id}/submit",
        json={
            "answers": [
                {
                    "question_id": questions[0]["id"],
                    "selected_option": (correct_by_id[questions[0]["id"]] + 1) % 4,
                }
            ]
        },
    )
    check(
        "a repeated submit returns the stored result",
        repeat_submit.status_code == 200
        and repeat_submit.json()["score"] == result["score"]
        and repeat_submit.json()["submitted_at"] == result["submitted_at"],
        f"HTTP {repeat_submit.status_code} score={repeat_submit.json().get('score')}",
    )

    result_again = student.get(f"/api/v1/student/attempts/{attempt_id}/result")
    check(
        "the result endpoint matches the submitted result",
        result_again.status_code == 200
        and result_again.json()["score"] == result["score"]
        and result_again.json()["percentage"] == result["percentage"]
        and result_again.json()["submitted_at"] == result["submitted_at"],
        f"HTTP {result_again.status_code}",
    )

    limit = teacher.patch(
        f"/api/v1/teacher/quizzes/{quiz_id}",
        json={"attempt_limit": 1},
    )
    check(
        "teacher can set a one-attempt limit",
        limit.status_code == 200 and limit.json()["attempt_limit"] == 1,
        f"HTTP {limit.status_code} attempt_limit={limit.json().get('attempt_limit')}",
    )
    over_limit = student.post(f"/api/v1/student/quizzes/{quiz_id}/attempts")
    check(
        "the attempt limit is enforced",
        over_limit.status_code == 409,
        f"HTTP {over_limit.status_code} {over_limit.text[:160]}",
    )

    regenerate = teacher.post(f"/api/v1/teacher/quizzes/{quiz_id}/regenerate")
    check(
        "published quizzes cannot be regenerated",
        regenerate.status_code == 409,
        f"HTTP {regenerate.status_code} {regenerate.text[:160]}",
    )

    print("\nChecking unrelease / re-release behaviour…")
    unrelease = teacher.post(f"/api/v1/topics/{topic['id']}/unrelease")
    check("topic unreleased", unrelease.status_code == 200, f"HTTP {unrelease.status_code}")
    unrelease.raise_for_status()

    after_unrelease = student.get("/api/v1/student/quizzes")
    after_unrelease.raise_for_status()
    check(
        "unreleasing the topic hides the quiz from the student list",
        str(quiz_id) not in {item["id"] for item in after_unrelease.json()},
        str([item["id"] for item in after_unrelease.json()]),
    )
    hidden_again = student.get(f"/api/v1/student/quizzes/{quiz_id}")
    check(
        "unreleased quiz detail returns 404",
        hidden_again.status_code == 404,
        f"HTTP {hidden_again.status_code}",
    )
    hidden_attempt = student.post(f"/api/v1/student/quizzes/{quiz_id}/attempts")
    check(
        "unreleased quiz cannot be started",
        hidden_attempt.status_code == 404,
        f"HTTP {hidden_attempt.status_code}",
    )
    result_after_unrelease = student.get(f"/api/v1/student/attempts/{attempt_id}/result")
    check(
        "submitted results survive an unrelease",
        result_after_unrelease.status_code == 200
        and result_after_unrelease.json()["score"] == result["score"],
        f"HTTP {result_after_unrelease.status_code}",
    )

    rerelease = teacher.post(
        f"/api/v1/topics/{topic['id']}/release",
        json={
            "week_no": 1,
            "due_at": (now + timedelta(days=7)).isoformat(),
            "generate_quiz": True,
        },
    )
    check(
        "re-release reuses the existing quiz",
        rerelease.status_code == 200
        and rerelease.json()["quiz_id"] == str(quiz_id)
        and rerelease.json()["quiz_queued"] is False,
        f"HTTP {rerelease.status_code} {rerelease.text[:200]}",
    )
    teacher_quizzes = teacher.get(f"/api/v1/teacher/quizzes?course_id={course_id}")
    teacher_quizzes.raise_for_status()
    matching = [
        item for item in teacher_quizzes.json()["items"] if item["topic_id"] == topic["id"]
    ]
    check(
        "re-release does not create a duplicate quiz row",
        len(matching) == 1,
        f"{len(matching)} quizzes for the topic",
    )
    restored = student.get("/api/v1/student/quizzes")
    restored.raise_for_status()
    check(
        "re-release restores student visibility",
        str(quiz_id) in {item["id"] for item in restored.json()},
        str([item["id"] for item in restored.json()]),
    )

    print("\nArtifacts left for inspection:")
    print(f"  document_id = {document_id}")
    print(f"  topic_id    = {topic['id']}")
    print(f"  quiz_id     = {quiz_id}")
    print(f"  attempt_id  = {attempt_id}")
    print(f"  teacher quiz page = http://localhost:3000/teacher/quizzes/{quiz_id}")

    if CLEANUP:
        print("\nSMOKE_CLEANUP is set; removing the smoke document…")
        cleanup_unrelease = teacher.post(f"/api/v1/topics/{topic['id']}/unrelease")
        if cleanup_unrelease.status_code != 200:
            note(f"cleanup unrelease returned HTTP {cleanup_unrelease.status_code}")
        deleted = teacher.delete(f"/api/v1/documents/{document_id}")
        note(
            "document deleted; the quiz row and attempt history remain "
            f"(HTTP {deleted.status_code})"
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
