# Demo script (10 minutes)

A scripted walkthrough of the three portals, the release gate, grounded RAG, and
audit/oversight. The dataset is produced by `scripts/demo_seed.py` through the
real HTTP API; nothing is inserted directly into Postgres.

## Before the demo

The 13 source PDFs are not in git. Place them at the repository root and verify
them first; `docs/corpus.md` explains the manifest and what to do on a new
machine.

```powershell
# 0. Confirm the local textbook corpus is present and unmodified
python scripts/verify_corpus.py

# 1. Start the stack (builds if needed)
docker compose --env-file .env -f infra/docker-compose.yml up -d --build

# 2. Apply migrations and the base seed (users, courses, enrollments)
docker compose --env-file .env -f infra/docker-compose.yml exec -T api alembic upgrade head
docker compose --env-file .env -f infra/docker-compose.yml exec -T api python -m app.seed

# 3. Build the demo dataset: 3 PDFs ingested, 2 topics released, quizzes,
#    attempts, 5 announcements. Idempotent; takes ~3 minutes on a cold stack.
backend\.venv\Scripts\python.exe scripts\demo_seed.py
```

`demo_seed.py` waits for PDF ingestion and quiz generation, so run it with a
valid `GROQ_API_KEY`. Re-running it only fills in missing pieces, including
regenerating a quiz that failed in an earlier attempt. On the reference VM a
full clean run (fresh database → demo ready) completed in **4.09 minutes**
(2026-11-06); the phase breakdown is recorded in `docs/runbook.md`.

## Accounts

| Role | Email | Password |
|---|---|---|
| Admin | `admin@lms.example.com` | `ChangeMe123!` |
| Teacher | `teacher1@lms.example.com` | `Passw0rd!` |
| Student (high accuracy) | `student01@lms.example.com` | `Passw0rd!` |
| Student (mid accuracy) | `student03@lms.example.com` | `Passw0rd!` |
| Student (low accuracy) | `student05@lms.example.com` | `Passw0rd!` |

These are demo credentials. Rotate them before any non-demo use.

## The 10-minute click path

### 0:00 — Teacher: ingestion and the source documents

1. Log in as `teacher1`. Open **Documents**.
2. Show three SCI101 chapters in `ingested` state (`hesc101`, `hesc102`,
   `hesc103`). Point out the page/chunk counts and that the third chapter is
   intentionally left unreleased.
3. Open a document's **Topics** view and show draft topics extracted from it.

**Message:** ingestion, chunking, and extraction are real; Postgres is the
source of truth and Chroma is a derived index.

### 2:00 — Teacher: the release gate

1. Open **Topics**. Show the first topic of chapters 1 and 2 as **Released**,
   each with a published task and an approved quiz.
2. Show chapter 3's topics still in **Draft**.
3. Optionally unrelease and re-release a topic to show the gate is reversible;
   the student view updates immediately.

**Message:** a topic is invisible to students until a teacher releases it. The
gate is enforced in DTOs, SQL, and the Chroma metadata filter.

### 3:30 — Student: tasks and the course view

1. Log in as `student01`. Open **Dashboard** and **Tasks**.
2. Show only the SCI101 published tasks (the demo dataset releases only the
   first topics of `hesc101`/`hesc102`); the unreleased chapter 3 produces no
   task and no notification.
3. Open **Library** and a released topic. Show the grounded excerpt and
   citations, with no `correct_option`/`explanation` fields anywhere.

### 5:00 — Student: take a quiz

1. Open **Tests**, start the approved SCI101 quiz.
2. Answer two questions, reload the page to show autosave, submit.
3. Show the server-graded result and per-question explanations.

### 6:30 — Student: study assistant (grounded + refusal)

1. Open **Assistant**. Ask: *"Summarise the key idea in this topic for a
   first-year student."*
2. Show the streamed answer and the document/page citations.
3. Ask about material in **chapter 3** (unreleased). The assistant refuses with
   no citations. This is the leak-gate demonstration.

**Message:** answers are grounded only in released chunks; the refusal is a
feature, not an error.

### 8:00 — Teacher: analytics

1. Log in as `teacher1`, open **Analytics**.
2. Show cohort KPIs, the sortable roster, the topic accuracy heatmap, and ranked
   knowledge gaps. Drill into `student05` (low accuracy) to show the
   per-student view.
3. Note that results are cached for 60 seconds and invalidated by release,
   completion, and submission events.

### 9:00 — Admin: oversight

1. Log in as `admin@lms.example.com`.
2. Open **Users** and show the role/course matrix; the permission matrix matches
   real behavior gates.
3. Open **Audit** and show the entries produced by this demo (release, quiz
   approval, attempt submission).
4. Open **Content** to show the cross-course document repository and
   vector-aware delete/reingest.
5. Open **System health** to show Postgres/Redis/Chroma dependency status.

### 10:00 — Close

Recap: role segregation (three portals, cross-portal redirects), the
multi-layer release gate, grounded citations with a 0 leak rate, and the
audit trail. Mention `SECURITY.md` and `docs/performance.md` for the hardening
evidence.

## Reset

To return to the pristine post-seed state:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml down -v
docker compose --env-file .env -f infra/docker-compose.yml up -d --build
docker compose --env-file .env -f infra/docker-compose.yml exec -T api alembic upgrade head
docker compose --env-file .env -f infra/docker-compose.yml exec -T api python -m app.seed
backend\.venv\Scripts\python.exe scripts\demo_seed.py
```

`down -v` deletes Postgres, Redis, Chroma, and upload volumes. Confirm before
running it on anything you care about.
