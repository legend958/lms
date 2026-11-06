# LMS — RAG-powered learning platform

A multi-portal learning management system where students ask questions against course
PDFs and get grounded, cited answers. Retrieval is restricted to topics a teacher has
explicitly released, so unreleased material can never leak into an answer.

**Stack:** FastAPI + SQLAlchemy 2 (async) + Postgres + Alembic · Next.js 15 App Router +
TypeScript + TanStack Query + Tailwind · Redis/ARQ worker · ChromaDB + local
`sentence-transformers/all-MiniLM-L6-v2` (Phase 2) · Groq for topic extraction (Phase 3),
quiz generation (Phase 5), and answer generation (Phase 6).

## Current state

| Phase | Scope | Status |
| --- | --- | --- |
| P0 | Monorepo, config, async DB layer, migrations, health checks, Docker Compose, CI | Done |
| P1 | Auth (Argon2id + rotating refresh tokens), RBAC, courses/enrollments, announcements, audit log, three portals | Done |
| P2 | PDF upload, extraction, section-aware chunking, MiniLM embeddings, Chroma indexing, teacher documents UI | Done |
| P3 | Topic extraction, review/merge/split, selective release, the release gate, hourly vector reconciliation | Done |
| P4 | Student tasks and dashboard, completion tracking, notification fan-out, unread bell | Done |
| P5 | Grounded MCQ generation, teacher review/publish, graded student attempts | Done |
| P6 | Study assistant: release-gated retrieval, cited answers, SSE streaming, RAG eval gates | Done |
| P7 | Teacher analytics: cohort KPIs, sortable roster, topic heatmap, knowledge gaps, drill-down | Done |
| P8 | Admin oversight: permission matrix, audit viewer, content repository, CSV import, system health | Done |
| P9 | Portal segregation, accessibility, performance evidence, security review, RAG sign-off, e2e, runbook | Done (one performance target open — see `docs/performance.md`) |

Phase 1 is fully usable: admins manage users and courses, teachers publish announcements,
students see only their courses and released announcements. Phase 2 adds PDF ingestion:
teachers upload course PDFs and watch them move through extraction, chunking, and embedding
into Chroma. Phase 3 turns those chunks into reviewable draft topics, creates a published
task when a teacher releases one, and enforces the release gate in the DTOs, SQL, service
layer, and Chroma metadata filter. Phase 4 adds the student task list and dashboard:
published tasks from released topics across every enrolled course, idempotent completion,
overdue derivation, and a notification bell fed by release fan-out. Phase 5 adds grounded
MCQs: a release can generate a draft quiz from the topic's chunks, teachers review and publish
it, and students take autosaved, server-graded attempts with per-question explanations. Phase 6
adds the study assistant: questions are answered only from released chunks with document/page
citations, streamed over SSE, and refused when nothing relevant is released. Phase 7 adds the
teacher analytics workspace: cohort KPIs, a sortable student roster, topic accuracy heatmap,
ranked knowledge gaps, and a per-student drill-down, all aggregated in SQL and cached for 60
seconds. Phase 8 adds admin oversight: a capability matrix with real behavior gates, an
append-only audit viewer with before/after diffs, a cross-course content repository with
vector-aware deletion, CSV user import with per-row reports, and dependency health. Phase 9
completes the hardening pass: role-segregated server layouts, an accessibility and security
review, hot-path query plans and indexes, a 41-case RAG evaluation, Playwright journeys across
all three portals, and the operations runbook and demo script.

## Ports

| Service | Port |
| --- | --- |
| Web | 3000 |
| API | 8000 (`/docs` for OpenAPI) |
| Postgres | 5432 |
| Redis | 6379 |
| Chroma | 8001 (host) → 8000 (container) |

## Quick start (Docker)

Requires Docker Desktop with Compose v2.

```bash
cp .env.example .env
# Set a real SESSION_SECRET (32+ characters) before sharing the stack.

docker compose --env-file .env -f infra/docker-compose.yml up -d --build
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.seed
```

The 13 textbook PDFs used by ingestion and `scripts/demo_seed.py` are **not
committed** (third-party copyright). Place them at the repository root and run
`python scripts/verify_corpus.py`; see `docs/corpus.md`. Once ingested, retrieval
is served from Postgres and Chroma and does not re-read the PDFs.

Open http://localhost:3000 and sign in with a demo account below. Compose builds the web
image with `API_INTERNAL_URL=http://api:8000`; Next bakes rewrites at build time, so rebuild
the web image if you change the API host.

The backend image bakes `all-MiniLM-L6-v2` into `/models` and sets `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1`. Keep those flags after the model-bake step: without them a container
with restricted egress retries the Hugging Face Hub for minutes before falling back to the local
weights. The API warms the embedder in the background at startup, so wait for
`assistant_embedder_warmed` in the API logs before timing the first assistant question.

`scripts/dev.ps1` (Windows) and `scripts/dev.sh` wrap the common commands:

```powershell
./scripts/dev.ps1 up
./scripts/dev.ps1 seed
./scripts/dev.ps1 logs
```

```bash
./scripts/dev.sh up
./scripts/dev.sh seed
./scripts/dev.sh logs
```

The API container runs `alembic upgrade head` on start. Re-run it manually with
`./scripts/dev.ps1 migrate`.

With the stack up and seeded, `scripts/smoke_phase4.py` runs the full Phase 4 path against
real Postgres, the ARQ worker, MiniLM, Chroma, and Groq: it uploads a generated PDF to each
of two courses, waits for ingestion, asserts topic extraction came from the LLM, releases a
topic in each, then checks the student task list, overdue derivation, idempotent completion,
dashboard counts, notification fan-out and mark-read, and completion history across
unrelease/re-release. It runs 28 checks and exits non-zero if any fail, so it doubles as a
post-deploy gate. Set `LMS_BASE_URL` to point it at a non-default host.

```powershell
backend/.venv/Scripts/python.exe scripts/smoke_phase4.py
```

`scripts/smoke_phase5.py` is the Phase 5 post-deploy gate. It uploads one generated
photosynthesis PDF, waits for real ingestion and topic extraction, releases the topic with
`generate_quiz=true`, waits for the ARQ worker to call Groq, and then walks the teacher and
student paths: edit/reset approval, publish, draft invisibility, student payload leak checks,
idempotent start, autosave+submit merging, a hand-computed score, post-submit `409`, repeat
submit, `attempt_limit`, regenerate rejection, and unrelease/re-release. It runs 53 checks and
exits non-zero if any fail. Pass `SMOKE_CLEANUP=1` to unrelease and delete the smoke document
at the end; by default the quiz and attempt history stay available for UI inspection.

```powershell
backend/.venv/Scripts/python.exe scripts/smoke_phase5.py
```

## Quick start (local, no Docker)

Requires Python 3.12+ and Node 22+. The test suite and the Phase 1 path use SQLite, so you
can work without Postgres, Redis, or Chroma. Phase 2 ingestion needs a Chroma server and the
local embedding model; see step 4 below.

### 1. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
DATABASE_URL=sqlite+aiosqlite:///./lms.db
SESSION_SECRET=dev-secret-please-change-0123456789abcdef
REDIS_URL=
CHROMA_HOST=localhost
CHROMA_PORT=8001
```

### 2. Backend

```bash
cd backend
python -m venv .venv

# Windows
.\.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -e ".[dev]"
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload --port 8000
```

### 3. Web

In a second terminal:

```bash
cd web
npm install
npm run dev
```

Next.js proxies `/api/*` to `http://localhost:8000`, so cookies stay same-origin. Override
with `API_INTERNAL_URL` if the API is elsewhere.

### 4. Enable ingestion and topic extraction (optional)

The API starts without Chroma or Groq. Uploads are accepted, but a worker cannot index them
until Chroma is reachable, and topics use the heading fallback until `GROQ_API_KEY` is set.
For a single-node local setup, run Chroma and the pipeline in-process:

```bash
# terminal 3 (after `pip install -e ".[dev]"` in backend)
chroma run --path ./data/chroma --port 8001

# in .env
INGEST_QUEUE=inline
CHROMA_HOST=localhost
CHROMA_PORT=8001
HF_HOME=./data/models
GROQ_API_KEY=gsk_...          # optional: enables LLM topic titles
GROQ_MODEL=openai/gpt-oss-120b
GROQ_REASONING_EFFORT=medium  # study-assistant answer effort; low can drop citations
```

Restart the API. The first ingestion downloads `all-MiniLM-L6-v2` into `./data/models`
(about 90 MB); later runs load it from disk. `INGEST_QUEUE=auto` uses ARQ when `REDIS_URL`
is set and otherwise falls back to in-process work. `INGEST_QUEUE=disabled` records uploads
without running them, which is what the test suite uses. Without `GROQ_API_KEY`, ingestion
still reaches `ready` with heading-derived topics marked `needs_review`.

From `backend/`, the same pipeline is available without HTTP:

```bash
python -m app.cli ingest hesc103.pdf \
  --course-id <course-uuid> --user-email teacher1@lms.example.com
python -m app.cli reindex <document-uuid>
python -m app.cli vector-stats
python -m app.cli vector-stats --document-id <document-uuid>
python -m app.cli vector-stats --topic <topic-uuid>   # released/hidden vector counts
```

## Demo accounts

Seeded by `python -m app.seed`:

| Role | Email | Password |
| --- | --- | --- |
| Admin | `admin@lms.example.com` | `ChangeMe123!` (or `SEED_ADMIN_PASSWORD`) |
| Teacher | `teacher1@lms.example.com` | `Passw0rd!` |
| Student | `student01@lms.example.com` | `Passw0rd!` |

The seed also creates 30 students, 3 teachers, and two courses: `SCI101` and `MATH101`.
Teachers 1–2 teach `SCI101`; teachers 2–3 teach `MATH101`. Students alternate courses, and
every fifth student is enrolled in both.

> Seed emails use `example.com` because `EmailStr` rejects reserved TLDs such as `.local`.

## What Phase 1 implements

### Identity and sessions

- Argon2id password hashing.
- 15-minute HS256 access JWT, 7-day rotating refresh token (only a SHA-256 hash is stored).
- `httpOnly` cookies: `lms_access`, `lms_refresh`, `lms_role`; readable `lms_csrf` for the
  double-submit CSRF token.
- Unsafe methods require `X-CSRF-Token` matching `lms_csrf`. Login and `/auth/csrf` are
  exempt so a client can bootstrap.
- Login rate limit: 5/minute and 10/hour per IP+email (in-memory in Phase 1; Redis-backed
  later).
- Login success/failure, refresh reuse, logout, user changes, course changes, enrollment
  changes, and announcement changes are written to `audit_logs`.

### Authorization

- Roles: `student`, `teacher`, `admin`.
- `CourseScope` resolves course visibility once per request:
  - admin → all courses;
  - teacher/student → only courses with an enrollment row.
- Non-members receive **404**, not 403, so course existence is not leaked.
- Students cannot publish announcements or manage courses. Teachers can publish only to
  courses they teach; admins can publish institution-wide.

### Courses and enrollments

- Course = offering (`code`, `title`, `term`, `description`, `status`).
- Many-to-many enrollments with `course_role` (`student`/`teacher`).
- Admin-only create/update/archive and add/remove enrollments.
- Archived courses disappear from scoped visibility but remain for audit.

### Announcements

- Institution-wide (`course_id = null`) or course-scoped.
- Optional `publish_at` and `expires_at`; students only see currently-published items.
- Staff can request `?scheduled=true` to see future and expired posts.
- Per-user read tracking; marking read is idempotent.
- Cursor pagination on `(created_at, id)`.

### Portals

- **Student:** dashboard with task progress, due-soon list, announcements, and unread
  notifications; task list with All/Incomplete/Completed tabs, course filter, sort,
  optimistic completion, and empty states; course list; study assistant with release-gated
  answers and document/page citations.
- **Teacher:** course-scoped PDF library with drag/drop upload, per-file progress, live
  ingestion stage, retry/delete, topic review and release control, quiz authoring, announcements
  composer with scheduling, and a SQL-aggregated analytics workspace with roster, heatmap, gaps,
  and student drill-down.
- **Admin:** user CRUD with role/status and course enrollment at creation, CSV import, course
  cards with enrollment management and archive/restore, announcements, a cross-course content
  repository with vector-aware delete/download/re-ingest, a capability matrix, an append-only
  audit viewer, and dependency health.

## What Phase 2 implements

### Upload and validation

- `POST /api/v1/documents` accepts one PDF per request with `course_id`; the web UI batches
  up to 20 files, each ≤ 50 MB and ≤ 1000 pages.
- MIME, `%PDF-` magic bytes, empty files, encrypted PDFs, and corrupt PDFs are rejected with
  a stable `422` problem response before storage.
- SHA-256 checksums deduplicate within a course. Re-uploading the same PDF returns `409`
  with the existing `document_id`; a soft-deleted copy does not block a fresh upload.
- Files are stored through a `Storage` adapter (local filesystem or S3-compatible) at
  `documents/{course_id}/{document_id}.pdf`. Postgres remains the source of truth.

### Ingestion pipeline

- PyMuPDF extraction keeps page boundaries and heading offsets, filters repeated page
  headers/footers, collapses duplicate lines, and flags image-only pages.
- Structure-aware chunking targets ~600 tokens with ~90-token overlap, never crosses a
  section heading, and records page spans plus section titles.
- Local `sentence-transformers/all-MiniLM-L6-v2` produces 384-dimensional normalized
  vectors. The model loads lazily only in the worker/CLI, never during API startup. Because
  MiniLM's context is 256 tokens, longer chunks are embedded as overlapping token windows
  and mean-pooled, so the whole chunk remains searchable rather than only its first 256
  tokens.
- Chunks are written to Postgres first, then upserted to the Chroma `lms_chunks` collection
  with scalar metadata (`course_id`, `document_id`, `chunk_index`, `page_start`, `page_end`,
  `section_title`, `released=false`, `topic_id`). Every vector starts hidden.
- Statuses move `queued → extracting → chunking → embedding → topic_extraction → ready`;
  failures record `error_code`/`error_detail` and can be retried. ARQ retries transient
  failures up to three attempts, resetting partial work between attempts.
- Soft delete removes vectors and the stored file best-effort while retaining DB rows for
  audit. Retry is allowed only for `failed` documents and creates a fresh ingestion job.
- Re-ingesting or deleting a document is refused while any of its topics is released
  (`RELEASED_TOPICS` / `409`), so a release can never be invalidated silently.

### Operations

- `/readyz` now checks Postgres, Redis (when configured), and Chroma.
- The teacher documents page polls the list and per-document status endpoints every two
  seconds while work is active.
- Ingestion errors use stable codes: `ENCRYPTED_PDF`, `NO_TEXT_LAYER`, `PARSE_FAILED`,
  `EMBEDDING_FAILED`, `VECTOR_WRITE_FAILED`, `STORAGE_FAILED`, `QUEUE_UNAVAILABLE`,
  `RELEASED_TOPICS`, `UNKNOWN`.

## What Phase 3 implements

### Topic extraction

- After embedding, the worker asks Groq for a strict JSON topic list (title, summary, page
  range, chunk IDs) at `temperature=0.1` with two retries. Large documents are mapped in
  batches and reduced, so a 1000-page PDF stays within one prompt budget.
- Every proposal is validated in code: unique titles, known chunk IDs, page ranges inside
  the document, no chunk assigned twice, and at least 90% chunk coverage. Unmapped chunks
  attach to the nearest topic and mark it `needs_review`.
- If `GROQ_API_KEY` is unset or extraction fails, a deterministic heading-based fallback
  groups chunks by section and marks every topic for review. Ingestion still reaches
  `ready`; it never fails because the LLM is unavailable.
- Topics are persisted as `draft` with `documents.topics_source` set to `llm` or
  `fallback`. Nothing is released automatically.

### Review, release, and the gate

- `GET /api/v1/documents/{id}/topics` lists draft/released topics with chunk counts and task
  state. Teachers can edit titles/summaries/order, merge drafts, split a draft at a chunk,
  delete a draft (its chunks move to a neighbour), and preview the exact student payload.
- Release creates one published task (`week_no`, `due_at`), flips the topic and its chunks
  to released, then updates Chroma metadata. A Chroma failure does **not** roll back the
  release; it queues an hourly reconciliation instead.
- Unrelease fails closed: Chroma is updated first, then Postgres. If Chroma is down the
  request returns `503` and the topic stays released and consistent.
- The gate is enforced independently in four places: student DTOs (`release_state` and
  audit fields absent), the SQL repository (`TopicReleaseState.RELEASED` + non-deleted
  document), the service guard (`assert_topic_released`), and the Chroma filter
  (`student_vector_where`: course scope + `released=true`).
- Release also accepts `generate_quiz` (default `true`). When enabled it creates one draft quiz
  for the topic inside the release transaction and enqueues `generate_quiz` after commit. An
  enqueue failure never fails the release; the draft remains for a teacher-triggered regenerate.
- The teacher UI lives at `/teacher/documents/[id]/topics` and is linked from
  `/teacher/documents` once a document is `ready`.

## What Phase 4 implements

### Student tasks and completion

- `task_progress` stores one row per `(task_id, student_id)` with `status`
  (`completed`/`incomplete`), `completed_at`, and `updated_at`. Completing twice is
  idempotent and preserves the original `completed_at`; progress rows are never deleted.
- The student task query joins `tasks → topics → documents → enrollments`, filtered by
  `topics.release_state='released'`, `tasks.status='published'`, an active course, and a
  non-deleted document, with a left join to the caller's progress row. A client-supplied
  `course_id` is validated against `CourseScope` and returns `404` when foreign.
- Status is derived per request: `completed` from the progress row, otherwise `incomplete`;
  `overdue` is `due_at < now` and not completed. Filters are `all|incomplete|completed`; sort
  is `due_at` (ascending) or `released_at` (newest release first).
- Unreleasing a topic archives its task, so it disappears from student lists, but the
  progress row survives and reappears if the topic is released again.

### Dashboard and notifications

- `GET /api/v1/student/dashboard` returns pending/completed/overdue counts, the next three
  incomplete tasks by due date, and the latest three visible announcements.
- Release fan-out inserts one `task_released` notification per actively enrolled student in
  the same transaction as the release. Releasing an already-released topic does not fan out
  again.
- `GET /api/v1/notifications?unread=true` returns the caller's latest 50 notifications plus
  an `unread_count`; `POST /api/v1/notifications/{id}/read` is idempotent and `404`s for
  another user's row. The shell shows an unread badge and a panel with per-item and
  mark-all-read actions.
- Student DTOs remain release-safe: no `release_state`, `released_by`, `released_at`, chunk
  metadata, or vector identifiers appear in task, dashboard, or notification payloads.

## What Phase 5 implements

### Grounded generation

- Release can create one draft quiz per topic (`quizzes.topic_id` is unique) and enqueue the ARQ
  `generate_quiz` job after the release transaction commits. Generation state is observable as
  `queued → generating → ready|failed` with a `generation_error` the teacher UI surfaces.
- `app/rag/quizzes.py` builds context only from the topic's own chunks and validates every
  proposal before persistence: four distinct options, one valid `correct_option`, a known
  `source_chunk_id` from that topic, a bounded stem, no duplicate stem, and no trivially negated
  option pair. Invalid proposals are dropped, and the generator retries until it reaches the
  target count or exhausts its rounds.
- A question cannot exist without a valid source chunk. MCQ only: no short-answer or LLM
  grading in v1.

### Teacher review and publishing

- `GET /api/v1/teacher/quizzes` lists quizzes with question/approved counts, and the review page
  polls `GET /api/v1/teacher/quizzes/{id}` while generation is in flight.
- Teachers can edit a question, delete it, toggle `approved`, change quiz settings, regenerate a
  draft, or approve the quiz. Editing a question resets its `approved` flag. Approval publishes
  the quiz and marks every question approved; `auto_approve` is off by default.
- A published quiz cannot be regenerated; questions of a published quiz cannot be edited.
  Unreleasing the topic hides the quiz without deleting the quiz or attempt history; re-releasing
  reuses the same quiz row and does not enqueue a second generation.

### Student attempts

- Student visibility requires `quizzes.status='published'`, a released/non-deleted topic, a
  non-deleted document, an active course, and an active enrollment; otherwise the quiz is `404`.
- `POST /api/v1/student/quizzes/{id}/attempts` is idempotent: if an open attempt exists it is
  returned, so a refresh resumes instead of creating a second attempt. A partial unique index,
  `uq_active_attempt`, enforces one open attempt per `(student_id, quiz_id)` at the DB level.
- `PATCH /api/v1/student/attempts/{id}` autosaves a partial or full answer set. Submit merges the
  request with autosaved answers, grades deterministically on the server, and writes a row for
  every question—unanswered questions are stored as `selected_option=null`, `is_correct=false`.
- After submit, saves return `409` and a repeated submit returns the stored result. The review
  payload includes `correct_option`, `explanation`, and `source_chunk_id`; the start, autosave,
  and pre-submit detail payloads never do.

### Web

- Teacher: `/teacher/quizzes` list and `/teacher/quizzes/[id]` review page.
- Student: `/student/tests` list, `/student/tests/[quizId]` intro/start, and
  `/student/tests/attempts/[attemptId]` runner/result. The runner autosaves 700 ms after the
  last selection.

## What Phase 6 implements

### Release-gated retrieval

- `app/rag/retriever.py` embeds the question with MiniLM, queries Chroma for `top_k=12` with
  `course_id IN scope AND released=true` (plus an optional `topic_id`), converts cosine distance
  to `similarity = 1 - distance` clamped to `[0, 1]`, drops hits below
  `CHROMA_MIN_SIMILARITY` (0.30), applies MMR (`RAG_MMR_LAMBDA=0.7`), and keeps the top 6.
- Every surviving vector ID is resolved back to Postgres before prompt assembly. The query
  re-checks `chunks.released`, `topics.release_state='released'`, the topic/document soft-delete
  flags, `documents.status='ready'`, and the caller's course scope. A vector whose metadata says
  released but whose Postgres row disagrees is dropped and logged.
- No hit above the threshold returns the fixed refusal with zero citations. The assistant never
  answers from model knowledge or from an unreleased chunk.

### Grounded answers and citations

- `app/rag/generator.py` assembles numbered `<ctx id doc page section>` blocks and calls Groq
  with `temperature=0.2`, `max_tokens=1024`, and bounded retries. The system prompt allows only
  the supplied excerpts and requires `[n]` citations.
- Generation is non-streaming internally so the complete answer can be validated before the
  first byte reaches the student. Citation markers are checked against the retrieved set,
  invalid markers are stripped, and an answer with zero valid citations is downgraded to the
  refusal. Unicode bracket variants such as `【1】` are normalized to `[1]` so a correct answer
  is not rejected on typography alone.
- Chunks containing prompt-injection phrases are still usable as data but set
  `injection_flag=true` on the query log for review. PDF text is never treated as instructions.

### SSE endpoint, limits, and logging

- `POST /api/v1/student/ask` runs auth, scope, topic visibility, validation, rate limiting,
  retrieval, generation, and logging before streaming. The response then emits `token` events,
  a terminal `citations` event, and a `done` event. A generation failure emits `error` first but
  still sends `citations` and `done`.
- The limiter is 20 queries/hour/student, Redis-backed when `REDIS_URL` is set with an
  in-process fallback for single-worker development and tests.
- `rag_query_logs` stores `question_hash`, chunk IDs, `max_similarity`, `refusal`,
  `injection_flag`, latency, token counts, and `prompt_version`. Raw `question_text` is stored
  only when `RAG_STORE_QUESTIONS=true`; the default is off.
- The API warms the embedding model in the background after startup, so the first question does
  not pay the model-load cost. Answers use `GROQ_REASONING_EFFORT=medium` by default, which keeps
  generation inside the 8-second RAG latency target. `low` is not recommended: live checks showed
  it sometimes omits citations, which the validator correctly turns into a refusal.
- Measured on the final Compose image through the web proxy: covered questions answered in
  1.8–3.0 s wall time (1.6–2.8 s server-side); draft-topic and uncovered refusals returned in
  under 0.2 s. Background embedder warmup completed in under 40 s.

### Web

- `/student/assistant` is a streaming chat page with citation chips (`[n] title · p. page`), a
  "released material only" disclaimer, course scope selector, suggested questions from released
  topics, a released-topic sidebar, stop control, and empty/error states. `/student/library`
  redirects here and the student nav label is "Study assistant".

### Evaluation

- `backend/eval/rag_cases.yaml` (41 cases) and `backend/eval/run_eval.py` run offline with an
  inline corpus, deterministic hashing embedder, in-memory SQLite, and a local cosine vector
  store. The suite measures hit@6, citation validity, refusal accuracy, and leak rate; it fails
  when a gate is missed. One corpus case deliberately marks a draft topic's vector metadata as
  released, proving the Postgres re-check is the final authority.

## What Phase 7 implements

### SQL aggregation and scoping

- `app/services/analytics_service.py` computes every KPI, student row, topic cell, gap, and
  drill-down with SQL aggregates on read. There is no client math and no materialized view.
- Completion uses `active students × published tasks from released, non-deleted topics` as the
  denominator. The week filter applies `tasks.week_no` to completion and to attempts tied to that
  week's tasks.
- Student rows add `days_active` from `count(distinct date(task_progress.updated_at))` unioned with
  `quiz_attempts.submitted_at`. Average score is the mean of each attempt's `score / max_score`.
- A student is `at_risk` when their average is below 60% on at least two distinct topics.
  Topic and question gaps are returned worst-accuracy-first.
- `last_active_at` is written by `get_current_user` at most once per five minutes and indexed by
  `ix_users_last_active_at` (migration `0009`).
- Course ownership goes through `CourseScope.require`. A course outside a teacher's scope returns
  `404 Course not found` (no existence leak), students get `403` from the role dependency, and
  admins may read every course.

### Cache

- Responses are cached under `analytics:{course_id}:{filter_hash}` with a 60-second TTL. Redis is
  used when `REDIS_URL` is set; development and tests fall back to an in-process TTL dictionary.
- Topic release/unrelease, student task completion, and attempt submission invalidate every
  cached filter variant for the affected course.

### Web

- `/teacher/analytics` renders KPI cards, a server-sorted student table (name, completion, tasks,
  average score, days active, last active, risk signal), a topic accuracy heatmap with 80%/60%
  buckets, ranked topic/question gaps, and an inline drill-down with task history, scored attempts,
  weakest questions with explanations and sources, plus course/week filters and empty states.

### Verification

- `backend/tests/test_analytics.py` builds a hand-computable fixture: 10 students, 3 released
  topics, 1 task and 2 submitted attempts per student/topic. It asserts the exact cohort
  (`students=10`, `completion_rate=63.3`, `completed=19`, `pending=11`, `avg_score=64.2`,
  `active_today=4`), topic cells (`90.0/60.0/40.0` completion, `90.0/52.5/50.0` accuracy), stable
  cursor pagination, drill-down contents, ownership (`404`), role enforcement (`403`), and cache
  invalidation through the real completion endpoint. That is 8 Phase 7 tests.
- Live cross-check on the Compose database: `SCI101` returned `students=18`, `completed=1`,
  `pending=53`, `avg_score=50.0`, and `active_today=1`, matching independent `psql` aggregates,
  with a foreign course returning `404` and a student receiving `403`. The four endpoints and the
  `/teacher/analytics` page were also exercised through the web proxy.
- Performance: a temporary 500-student, 3-topic, 1,000-attempt benchmark course measured
  cold-cache P95 of `118.8 ms` (KPIs), `211.6 ms` (roster), `192.8 ms` (topics), and `365.0 ms`
  (drill-down), well under the 1.5 s target. The benchmark rows were deleted afterwards.

## What Phase 8 implements

### Capability matrix

- `permissions(key, role, enabled)` is keyed by capability and seeded by migration `0010` with
  `student.self_enroll` (off), `teacher.create_course` (off), `student.assistant_enabled` (on),
  and `quiz.auto_approve` (on). Missing rows fall back to the same defaults, so a fresh test
  database behaves exactly like a migrated one.
- `PermissionService.can(role, key)` reads through a 30-second in-process cache and is invalidated
  on every write. Admins always bypass the matrix; only the student and teacher portals are gated.
- Each flag has real behavior: the assistant route returns `403` when disabled, quiz generation
  stops auto-publishing when disabled, teachers can create and self-join courses when enabled, and
  students can self-enroll into active courses when enabled.
- Permission writes are audited with the previous and next values. The UI confirms a toggle that
  will affect other accounts and shows how many active users are impacted.

### Audit viewer

- `GET /api/v1/admin/audit-logs` filters by actor, action, entity type/id, and a `from`/`to` window,
  ordered newest-first with cursor pagination. The actor name is joined in SQL.
- The UI renders an expandable field-by-field diff (`before` vs `after`) and shows actor, IP,
  request id, and entity. There is deliberately no delete endpoint; audit rows survive user and
  document deletion.
- Admin mutations covered by tests include user create/update/delete, CSV import, permission
  update, document re-ingest/download/hard-delete, and self-enrollment. Quiz approval and the
  existing course/announcement/document/topic/quiz actions keep their original audit records.

### Content repository

- `GET /admin/content` aggregates course, uploader, page count, status, topic count, released-topic
  coverage, and chunk count in SQL. Filters: `course_id` and `status`.
- `POST /admin/content/{id}/reingest` queues a fresh ingestion job and audits the transition.
- `GET /admin/content/{id}/download` returns a five-minute signed URL (local filesystem URI in
  development, S3 presigned URL in production) and audits the access.
- `DELETE /admin/content/{id}` is a hard delete after the released-topic guard: Postgres cascades
  through attempts, quizzes, questions, tasks, topics, chunks, and ingestion jobs; the Chroma
  vectors and stored PDF are removed too. The audit row keeps the pre-delete snapshot.

### CSV import and health

- `POST /admin/users/import` accepts `name,email,role[,course_codes]`, reports every row as
  `created`, `skipped`, or `error` with a reason, and returns temporary passwords once. Malformed
  emails/roles/names and unknown course codes are row errors; existing and in-file duplicate
  emails are skipped. `mode=partial` imports valid rows; `mode=atomic` imports nothing when any
  row fails. The whole attempt is audited with counts and created user ids.
- `GET /admin/system/health` checks Postgres, Redis, Chroma, and Groq, reports queue depth and the
  latest ingestion failure. Groq is reported as `degraded` and returns HTTP 200; it is a quality
  dependency, not a readiness gate.

### Web

- `/admin/content` lists every document with course/status filters, release coverage, a health
  strip, and download/re-ingest/delete actions guarded by confirmations and the released-topic
  rule.
- `/admin/permissions` renders the capability matrix with toggle switches and affected-user
  counts. `/admin/audit` provides filters, cursor paging, and expandable before/after diffs.
  `/admin/users` adds a drag-and-drop CSV import dialog with mode selection and a per-row result
  table.

### Verification

- `backend/tests/test_permissions.py`, `test_admin_audit.py`, and `test_bulk_import.py` add 25
  Phase 8 tests. They cover admin-only enforcement, seeded defaults, admin bypass, real behavior
  changes for every capability, audit coverage, append-only behavior, cursor filtering, CSV
  partial/atomic semantics, malformed rows, temporary-password login, content aggregation, the
  released-topic delete guard, and vector/storage/row removal.
- Full backend gate: **195 tests passed**, `ruff` clean, `mypy` clean (97 source files). Web gate:
  `tsc --noEmit` and `next build` clean.

## What Phase 9 implements

### Portal segregation

- Routes are grouped as `(student)`, `(teacher)`, and `(admin)` with async server
  layouts. Each layout reads the `lms_role` cookie through `requireRole()` and
  redirects to `/login` when absent or to the user's own portal on a mismatch.
  `PortalShell` repeats the check against the live `GET /auth/me` payload, so a
  stale cookie cannot render another portal.
- The API remains authoritative: every route re-validates the session, role, and
  course scope. The web guards are a UX boundary, not the security boundary.
- Navigation gained breadcrumbs, `aria-current` active states, a skip link, a
  `Modal` with a focus trap, toast live regions, loading skeletons, and
  404/500/global-error pages.

### Accessibility, security, and operations

- `docs/a11y.md` records the WCAG 2.1 AA spot-check: keyboard-only passes, focus
  management, landmarks, names/roles, live feedback, reflow, and a contrast
  table. `SECURITY.md` records the threat model, controls, dependency audit, and
  remaining risks.
- Containers run non-root with a read-only root filesystem, `cap_drop: [ALL]`,
  `no-new-privileges`, and tmpfs mounts. Only the uploads volume is writable.
- `docs/api.md`, `docs/runbook.md`, and `docs/demo.md` document the API surface,
  backup/restore/reindex/rotation procedures with a drill log, and a
  10-minute demo script backed by `scripts/demo_seed.py`.

### Performance and RAG evidence

- `load/explain.sql` covers six hot-path plans; migration `0011` adds
  `ix_attempt_answers_question_id` and `ix_quiz_attempts_student_started`.
- `docs/performance.md` records the k6 runs, the GC/`gc.freeze()` fix after
  embedder warmup, the access-log and pool measurements, and the worker ceiling.
  The RAG target (2 req/s, P95 < 8 s) passes; **the CRUD target (50 req/s,
  P95 < 400 ms) does not pass on the 2.78 GiB reference VM** and is reported as
  an open gap with remediation rather than hidden.
- The RAG eval suite reports hit@6 1.000, citation validity 1.000, refusal
  accuracy 1.000, **leak rate 0.000**, and prompt-injection flagged 1/1.
- Playwright runs the three-portal journeys serially against the hardened stack:
  8/8 passing.

See `SECURITY.md`, `docs/performance.md`, `docs/a11y.md`, `docs/rag-eval.md`,
`docs/api.md`, `docs/runbook.md`, and `docs/demo.md`.

## API surface (Phase 1–9)

| Method | Path | Access |
| --- | --- | --- |
| `GET` | `/healthz`, `/readyz` | Public |
| `POST` | `/api/v1/auth/login` | Public |
| `POST` | `/api/v1/auth/refresh` | Refresh cookie + CSRF |
| `POST` | `/api/v1/auth/logout` | Auth + CSRF |
| `GET` | `/api/v1/auth/me` | Auth |
| `GET` | `/api/v1/auth/csrf` | Public (sets CSRF cookie) |
| `GET/POST` | `/api/v1/admin/users` | Admin |
| `PATCH/DELETE` | `/api/v1/admin/users/{id}` | Admin |
| `POST` | `/api/v1/admin/users/import` | Admin (CSV, `mode=atomic\|partial`) |
| `GET` | `/api/v1/admin/audit-logs` | Admin |
| `GET` | `/api/v1/admin/content` | Admin |
| `DELETE` | `/api/v1/admin/content/{id}` | Admin |
| `POST` | `/api/v1/admin/content/{id}/reingest` | Admin |
| `GET` | `/api/v1/admin/content/{id}/download` | Admin |
| `GET/PATCH` | `/api/v1/admin/permissions` | Admin |
| `GET` | `/api/v1/admin/system/health` | Admin |
| `GET` | `/api/v1/courses` | Auth (scoped) |
| `GET` | `/api/v1/courses/{id}` | Auth (scoped) |
| `POST/PATCH/DELETE` | `/api/v1/courses[/{id}]` | Admin; teacher POST when `teacher.create_course` is enabled |
| `POST` | `/api/v1/courses/{id}/self-enroll` | Student, when `student.self_enroll` is enabled |
| `GET/POST` | `/api/v1/courses/{id}/enrollments` | Scoped read / admin write |
| `DELETE` | `/api/v1/courses/{id}/enrollments/{user_id}` | Admin |
| `GET/POST` | `/api/v1/announcements` | Scoped / staff |
| `PATCH/DELETE` | `/api/v1/announcements/{id}` | Author or admin |
| `POST` | `/api/v1/announcements/{id}/read` | Auth (scoped) |
| `GET/POST` | `/api/v1/documents` | Teacher/admin, course-scoped |
| `GET/DELETE` | `/api/v1/documents/{id}` | Teacher/admin, course-scoped |
| `GET` | `/api/v1/documents/{id}/status` | Teacher/admin, course-scoped |
| `POST` | `/api/v1/documents/{id}/retry` | Teacher/admin, failed only |
| `GET` | `/api/v1/documents/{id}/topics` | Teacher/admin, course-scoped |
| `PATCH` | `/api/v1/topics/{id}` | Teacher/admin, course-scoped |
| `POST` | `/api/v1/topics/merge` | Teacher/admin, same document |
| `POST` | `/api/v1/topics/{id}/split` | Teacher/admin, draft only |
| `POST` | `/api/v1/topics/{id}/release` | Teacher/admin, ready document |
| `POST` | `/api/v1/topics/{id}/unrelease` | Teacher/admin |
| `GET` | `/api/v1/topics/{id}/preview` | Teacher/admin, student-view payload |
| `GET` | `/api/v1/teacher/quizzes` | Teacher/admin, course-scoped |
| `GET` | `/api/v1/teacher/quizzes/{id}` | Teacher/admin, course-scoped |
| `GET/POST` | `/api/v1/teacher/topics/{topic_id}/quiz` | Teacher/admin, course-scoped |
| `PATCH` | `/api/v1/teacher/quizzes/{id}` | Teacher/admin, draft only |
| `POST` | `/api/v1/teacher/quizzes/{id}/approve` | Teacher/admin, draft only |
| `POST` | `/api/v1/teacher/quizzes/{id}/regenerate` | Teacher/admin, draft only |
| `PATCH/DELETE` | `/api/v1/teacher/questions/{id}` | Teacher/admin, draft quiz only |
| `GET` | `/api/v1/teacher/analytics` | Teacher/admin, course-scoped |
| `GET` | `/api/v1/teacher/analytics/students` | Teacher/admin, course-scoped |
| `GET` | `/api/v1/teacher/analytics/topics` | Teacher/admin, course-scoped |
| `GET` | `/api/v1/teacher/analytics/students/{id}` | Teacher/admin, course-scoped |
| `GET` | `/api/v1/student/topics` | Student, released only |
| `GET` | `/api/v1/student/topics/{id}` | Student, released only |
| `GET` | `/api/v1/student/dashboard` | Student, enrolled courses |
| `GET` | `/api/v1/student/tasks` | Student, released + published only |
| `PATCH` | `/api/v1/student/tasks/{id}` | Student, released + published only |
| `GET` | `/api/v1/student/quizzes` | Student, published + released only |
| `GET` | `/api/v1/student/quizzes/{id}` | Student, published + released only |
| `POST` | `/api/v1/student/quizzes/{id}/attempts` | Student, published + released only |
| `GET` | `/api/v1/student/attempts` | Student, own attempts |
| `GET` | `/api/v1/student/attempts/{id}` | Student, own attempt |
| `PATCH` | `/api/v1/student/attempts/{id}` | Student, own open attempt |
| `POST` | `/api/v1/student/attempts/{id}/submit` | Student, own open attempt |
| `GET` | `/api/v1/student/attempts/{id}/result` | Student, own submitted attempt |
| `POST` | `/api/v1/student/ask` | Student, released + enrolled scope (SSE) |
| `GET` | `/api/v1/notifications` | Auth (own rows only) |
| `POST` | `/api/v1/notifications/{id}/read` | Auth (own rows only) |

Errors use RFC 7807 `application/problem+json` with a `request_id`.

## Tests and checks

```bash
cd backend
ruff check app tests eval
ruff format --check app tests eval
mypy app
pytest -q
python -m eval.run_eval --suite rag_cases.yaml
```

```bash
cd web
npm run lint   # tsc --noEmit
npm run build
```

The backend suite runs on in-memory SQLite (`aiosqlite`, `StaticPool`) and covers health,
login/logout/refresh rotation, inactive users, rate limiting, CSRF, the RBAC matrix, course
visibility, enrollment changes, announcement windows, read tracking, edit permissions, PDF
extraction failures, heading-aware chunking, upload validation/deduplication, document
scoping, retry rules, soft delete, and the ingestion pipeline against fake embedder/vector
store adapters. Phase 3 adds topic validation and fallback, review/merge/split, the release
gate (DTOs, SQL, service guard, and vector filter), fail-closed unrelease, and vector
metadata reconciliation. Phase 4 adds multi-course task aggregation, status filters and
sort, idempotent completion, unreleased invisibility with retained history, cross-course
`404`s, dashboard counts, and notification fan-out/isolation. Phase 5 adds generation
validation/rejection, generation rounds, teacher edit/approve/publish, student DTO leak checks,
deterministic grading, one-open-attempt, post-submit immutability, attempt limits, cross-student
access, and unreleased-quiz invisibility—24 Phase 5 tests, 134 total. Phase 6 adds
release-gated retrieval, threshold and MMR behavior, the Postgres re-check over stale vector
metadata, Unicode citation normalization, zero-citation refusal, SSE terminal events, rate
limiting, raw-question privacy, and the Groq request shape—24 Phase 6 tests, 158 total. Phase 7
adds hand-computed cohort/topic/drill-down numbers, server-side sorting and cursor pagination,
course ownership, role enforcement, and cache invalidation through the completion endpoint—8
Phase 7 tests, 166 total. The offline RAG eval suite runs
41 cases against an inline corpus and enforces hit@6 ≥ 0.90, citation validity ≥ 0.98, refusal
accuracy ≥ 0.95, and a zero leak rate. Real Chroma, MiniLM, and Groq are exercised by the
local/Compose smoke path rather than CI, so the suite stays fast and offline.
`tests/test_migrations.py` runs the full Alembic chain against a throwaway SQLite file and
asserts every enum column is wide enough for its longest value, which catches Postgres-only
`VARCHAR` truncation that in-memory SQLite cannot.

CI (`.github/workflows/ci.yml`) runs lint/format/mypy, applies migrations to a Postgres
service, runs pytest, then type-checks and builds the web app.

## Project layout

```text
backend/
  app/
    api/            routers, schemas, deps, middleware, error handlers
    core/           config, security, roles, scope, rate limit, ids, logging, analytics cache
    db/             async engine, ORM models, pagination, repositories
    rag/            PDF extraction, chunker, embedder, Chroma adapter, topic + quiz extraction, assistant retrieval/generation, access filters
    services/       auth, users, courses, announcements, documents, ingestion, topics, tasks, notifications, quizzes, attempts, assistant, analytics, storage, audit
    workers/        ARQ queue, ingest + reconcile + generate_quiz jobs, worker settings
    cli.py          ingest, reindex, vector-stats
    main.py         app wiring, health/readiness
    seed.py         idempotent demo data
  eval/             offline RAG eval corpus, harness, and metric gates
  migrations/       Alembic env + baseline/identity/ingestion/topics/student-task/enum-width/quiz/rag-log/analytics-index migrations
  tests/            SQLite-backed pytest suite + generated PDF fixtures
web/
  app/              App Router pages for login, student, teacher, admin
  components/       portal shell, UI primitives, documents/topics/quizzes/assistant/analytics/admin/announcement/notification widgets
  lib/              API client with refresh + CSRF + upload progress + SSE streaming, hooks, types
infra/docker-compose.yml
docs/               api.md, performance.md, a11y.md, rag-eval.md, runbook.md, demo.md
load/               k6.js, baseline.js, explain.sql, sessions.json
scripts/            dev.ps1, dev.sh, demo_seed.py, k6_sessions.py, smoke_phase4/5/8.py
.github/workflows/ci.yml
prd.md · architecture.md · implementation.md · SECURITY.md
```

## Security invariants to preserve

1. A student must never see or retrieve an unreleased topic. This is enforced in the student
   DTOs, in SQL scoping, in the service guard, **and** in the Chroma metadata filter.
2. Student-facing DTOs must never expose `correct_option`, `explanation`, `source_chunk_id`,
   `release_state`, `released_by`, `released_at`, chunk/vector metadata, or internal vector
   identifiers before an attempt is submitted. Task, dashboard, notification, quiz-list, quiz-
   detail, start, and autosave payloads follow the same rule.
3. Students can only read, save, submit, or review their own attempts; a foreign attempt ID is
   `404`, not `403`. Grading and the answer key stay server-side.
4. An attempt is immutable after submission: autosave returns `409`, and a repeated submit
   returns the stored result instead of recomputing it.
5. A student must never see a quiz until it is published *and* its topic is released. Draft,
   unreleased, archived, and foreign-course quizzes are `404`.
6. Postgres is the source of truth. Commit there before updating Chroma; reconcile drift
   with a worker, never by trusting the vector store.
7. Production startup rejects the default `SESSION_SECRET` and requires
   `COOKIE_SECURE=true`.
8. Access tokens are short-lived and refresh tokens rotate; a replayed refresh token is
   revoked and audited.
9. The assistant searches only chunks that are both released and inside the student's enrolled
   course scope. Chroma's `released` metadata is never trusted alone: every candidate is
   re-resolved in Postgres, and nothing above threshold yields the refusal with zero citations.
10. Assistant query logs store a question hash, never raw question text by default. Unreleased
    chunk IDs must never appear in context, citations, or logs, and the assistant is limited to
    20 queries/hour/student.
11. Analytics are read-only and course-scoped: students never reach `/teacher/analytics`, and a
    teacher asking for a course they do not own receives `404`, never another cohort's numbers.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `503` on `/readyz` | Postgres, Redis, or Chroma is down. The JSON body names the failing check. |
| Login returns `422` for a valid-looking email | `EmailStr` rejects `.local`, `.test`, and similar reserved TLDs. Use a real domain or `example.com`. |
| `403` on POST/PATCH/DELETE | Missing `X-CSRF-Token`; the web client reads `lms_csrf` automatically. |
| Web shows “Could not load…” | Check the Next dev server can reach the API at `API_INTERNAL_URL`. |
| Upload returns `409` | The same PDF checksum is already active in that course; the response includes `document_id`. |
| Document stays `queued` | The worker is not running. Use Compose, set `INGEST_QUEUE=inline`, or enqueue to Redis. |
| Ingestion fails with `VECTOR_WRITE_FAILED` | Chroma is unreachable or the `lms_chunks` collection has the wrong dimension. |
| Release returns `409` | The document is not `ready`, or the topic is scheduled. Wait for topic review or unrelease first. |
| Unrelease returns `503` | Chroma could not be reached. The topic stays released and consistent; retry when Chroma is back. |
| Chroma and Postgres disagree about `released` | The hourly reconcile job repairs metadata from Postgres. Run `python -m app.cli vector-stats --topic <id>` to inspect. |
| Topic titles look generic | Groq was unavailable, so the heading fallback ran. Edit the topics or set `GROQ_API_KEY` and re-ingest. |
| First ingestion is slow | MiniLM weights are downloading into `HF_HOME`; later runs use the cache. |
| First assistant question is slow or `/readyz` lags | Wait for `assistant_embedder_warmed`. In Docker, confirm `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` are set after the baked-model step; otherwise the container may spend minutes retrying the Hugging Face Hub. |
| Quiz stays `queued`/`generating` | The worker is not running or Groq is slow. Check `docker compose --env-file .env -f infra/docker-compose.yml logs worker` and the quiz's `generation_error`. |
| Quiz generation `failed` | Usually an invalid/retired `GROQ_MODEL`, a bad key, or a topic with unusable chunks. Fix the model or key, then `POST /api/v1/teacher/quizzes/{id}/regenerate` while the quiz is still a draft. |
| `/student/tests` is empty | The quiz is still a draft, the topic was unreleased, or the student is not actively enrolled. Publish the quiz and release the topic. |
| Starting an attempt returns `409` | The quiz's `attempt_limit` has been reached. Raise it or let the student resume the existing open attempt. |
| `409` when saving an attempt | The attempt was already submitted. Results are immutable; start a new attempt if the limit allows. |
| Assistant always refuses | Confirm the topic is released, its document is `ready`, and the student is enrolled. Check `rag_query_logs.max_similarity` and `chunk_ids`; run `python -m app.cli vector-stats --topic <id>` and the eval suite. |
| Assistant refuses a correct-looking answer | The answer had no valid `[n]` citation after validation. Check `rag_query_logs` for the retrieved chunk IDs and `refusal`; the model must cite a number from 1 to the retrieved count. |
| `429` from `/student/ask` | The 20 queries/hour/student limit was reached. Wait for the window to reset or clear the limiter key in Redis. |
| `POST /student/ask` returns `404` | The `course_id` or `topic_id` is outside the student's enrolled scope; course scope is validated before streaming. |
| `alembic upgrade head` fails on SQLite | Run from `backend/` so `script_location = migrations` resolves. |
| Docker API never becomes healthy | `docker compose --env-file .env -f infra/docker-compose.yml logs api`; usually a migration or database password mismatch. |

## Status

All nine phases are implemented. The only unmet acceptance target is the
50 req/s CRUD P95 on the reference VM; it is documented with measurements and a
remediation path in `docs/performance.md`. Feature work should stop here; the
architecture document's §20 "Future Evolution" items (cohorts, richer
assessments, non-PDF ingestion, hybrid search, conversation memory, adaptive
practice, LTI/SCORM, managed vector DB) are intentionally not implemented.

Retrieval is deliberately single-turn: each question must be self-contained.
Follow-up questions are not rewritten from prior turns because conversation
memory is one of the deferred 20 items; the reasoning and the safe extension
path are recorded in `docs/rag-eval.md`.
