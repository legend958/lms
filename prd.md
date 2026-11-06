# Product Requirements Document (PRD)

## RAG-Powered Multi-Portal Learning Management System

| Field | Value |
|---|---|
| **Product** | Intelligent Multi-Portal LMS with RAG backend |
| **Document version** | 1.0 (Draft for review) |
| **Source** | `docs/problemstatement.txt` |
| **Status** | Proposed |
| **Last updated** | 2026-10-30 |
| **Owner** | Product / Engineering |
| **Primary stack constraints** | `sentence-transformers/all-MiniLM-L6-v2`, ChromaDB, Groq API |

---

## 1. Overview

### 1.1 Problem

Educational institutions and training programs run on disconnected tools: content lives in PDFs and shared drives, assignments are tracked in spreadsheets, quizzes are separate from the material they test, and instructors have no reliable view of who is actually learning. Traditional LMS platforms store content but do not *understand* it, so they cannot surface topics, generate assessments from source material, or answer a student's question in the context of the exact chapter they were assigned.

### 1.2 Solution

Build a single LMS with three role-scoped portals (Student, Teacher, Admin) backed by a Retrieval-Augmented Generation (RAG) pipeline. Teachers upload PDFs; the system extracts, chunks, embeds, and stores the content in a vector database, then proposes a structured topic list. Nothing reaches students until a teacher explicitly releases it. Released topics become weekly tasks, quizzes, and a grounded study assistant that answers only from released material and cites its sources.

### 1.3 Product Principles

1. **Release-gated by default.** Ingested content is invisible to students until a human releases it.
2. **Grounded, not generative.** Every AI answer and generated question must be traceable to source chunks in the uploaded corpus.
3. **One content pipeline, three portals.** Admin, Teacher, and Student views share one data model and one permission layer.
4. **Ship the whole flow first, then segment.** Phase 1 exposes all portals in one interface for end-to-end testing; role-based segregation follows.

---

## 2. Goals & Non-Goals

### 2.1 Goals

| ID | Goal | Measure |
|---|---|---|
| G1 | Ingest PDF course material into a searchable vector store with minimal instructor effort | 200-page PDF fully processed in < 5 minutes; ≥ 98% of pages yield text |
| G2 | Convert raw documents into a reviewable, editable topic structure | ≥ 80% of auto-extracted topics judged usable by a teacher without rework |
| G3 | Enforce a strict release gate between ingestion and student visibility | 0 student-visible artifacts from unreleased topics (verified by test suite) |
| G4 | Give students a clear weekly workload view and grounded assessments | Task list with status filters; quiz questions cite source topic/page |
| G5 | Give teachers actionable cohort analytics | Completion rate, pending work, engagement, and per-topic knowledge gaps on one dashboard |
| G6 | Give admins full lifecycle control | Full CRUD over users, roles, content, and permissions |

### 2.2 Non-Goals (v1)

- Live video classes, attendance, or proctoring.
- Mobile native apps (responsive web only).
- Payments, billing, or subscription management.
- Third-party LMS integration (SCORM, LTI, Moodle import).
- Handwritten work grading or subjective essay auto-grading.
- Multi-tenant white-labeling (single institution per deployment in v1).
- Non-PDF ingestion (DOCX, PPTX, video, audio) — designed for, not delivered.

---

## 3. Users & Personas

### 3.1 Student

> "I want to open the portal, see exactly what is due this week, do it, and know whether I'm on track."

Needs: distraction-free dashboard, announcements, weekly task list with status filters, quizzes tied to assigned content, progress visibility.

### 3.2 Teacher (Instructor)

> "I have a 300-page textbook. I want the system to tell me what's in it, let me pick what to teach this week, and show me who is falling behind."

Needs: PDF upload, automatic topic extraction, edit/merge/rename topics, selective release, quiz review, cohort analytics, knowledge-gap detection.

### 3.3 Administrator (Super User)

> "I need to onboard a class, fix a wrong role, remove stale content, and know nothing is leaking across cohorts."

Needs: user CRUD, role assignment, cohort/course management, content repository oversight, permission configuration, audit trail.

### 3.4 Role Capability Matrix

| Capability | Student | Teacher | Admin |
|---|---|---|---|
| View own profile | ✅ | ✅ | ✅ |
| View announcements | ✅ (published) | ✅ (own + institution) | ✅ (all) |
| Create/edit announcements | ❌ | ✅ (own cohorts) | ✅ |
| Upload PDFs | ❌ | ✅ (own courses) | ✅ |
| Run/retry ingestion | ❌ | ✅ (own documents) | ✅ |
| Extract & edit topics | ❌ | ✅ (own documents) | ✅ |
| Release / unrelease topics | ❌ | ✅ (own courses) | ✅ |
| View unreleased content | ❌ | ✅ | ✅ |
| Take quizzes | ✅ (released only) | ❌ (preview only) | ✅ (impersonate/preview) |
| View cohort analytics | Own only | ✅ (own cohorts) | ✅ (all) |
| Manage users & roles | ❌ | ❌ | ✅ |
| Configure permissions | ❌ | ❌ | ✅ |
| View audit logs | ❌ | ❌ | ✅ |

---

## 4. Core User Journeys

### J1 — Teacher: PDF → released task

1. Teacher signs in and selects a course/cohort.
2. Uploads one or more PDFs.
3. System extracts text, chunks, embeds, stores vectors, and returns a processing status.
4. System proposes topics (title, summary, page range, chunk count) mapped to source pages.
5. Teacher reviews the topic list, edits/merges/splits as needed, and attaches a due date and week.
6. Teacher releases selected topics.
7. System generates a task per topic (plus optional quiz) and makes it visible to enrolled students.
8. Teacher sees the released item in the cohort dashboard and analytics.

### J2 — Student: weekly work → quiz

1. Student signs in and lands on the dashboard: announcements + "Your Tasks".
2. Filters tasks by All / Incomplete / Completed; sees due dates and completion badges.
3. Opens a task, reads the assigned material summary/links, marks it complete.
4. Opens the Test section, takes the quiz for that topic.
5. Sees score, correct answers, and cited source pages; attempt is recorded for analytics.

### J3 — Teacher: identify a knowledge gap

1. Opens the cohort dashboard.
2. Sees completion rate, pending topics, last-active timestamps, and days active.
3. Drills into a student or a topic.
4. Sees quiz attempts and the topics/questions with the lowest accuracy.
5. Uses that signal to re-teach or release supplementary material.

### J4 — Admin: onboard a class

1. Admin creates a course/cohort.
2. Creates teacher and student accounts (or bulk-imports a CSV).
3. Assigns roles and enrollments.
4. Verifies permissions and content visibility via the audit log and preview mode.

---

## 5. Functional Requirements

Requirement IDs are stable and intended for issue tracking and test traceability.

### 5.1 Authentication, Roles & Access Control

| ID | Requirement | Priority |
|---|---|---|
| FR-AUTH-01 | Email + password authentication with hashed credentials (Argon2/bcrypt). | P0 |
| FR-AUTH-02 | Server-side sessions or signed JWT with refresh; logout invalidates session. | P0 |
| FR-AUTH-03 | Three roles: `student`, `teacher`, `admin`. Role is enforced server-side on every request, never only in the UI. | P0 |
| FR-AUTH-04 | Route and API guards redirect unauthorized users to their own portal home. | P0 |
| FR-AUTH-05 | Admin can force password reset and deactivate an account; deactivation blocks login immediately. | P1 |
| FR-AUTH-06 | Optional: SSO / OAuth (Google Workspace) for institutional deployments. | P2 |
| FR-AUTH-07 | Password reset via email token; token single-use and time-limited. | P1 |

### 5.2 Admin Portal

| ID | Requirement | Priority |
|---|---|---|
| FR-ADM-01 | CRUD for users (student, teacher, admin): create, list/search/filter, edit, deactivate, delete. | P0 |
| FR-ADM-02 | Bulk user import via CSV with per-row validation report. | P1 |
| FR-ADM-03 | CRUD for courses/cohorts; assign teachers and enroll students. | P0 |
| FR-ADM-04 | Content repository view: all documents, owner, size, ingestion status, topic count, release state. | P0 |
| FR-ADM-05 | Delete/re-ingest a document; deletion removes its chunks from the vector store. | P0 |
| FR-ADM-06 | Permission configuration: toggle portal capabilities per role (e.g., allow student self-enrollment). | P1 |
| FR-ADM-07 | Immutable audit log of privileged actions (user changes, releases, deletions, permission changes). | P1 |
| FR-ADM-08 | System health panel: DB, vector store, LLM provider status, ingestion queue depth. | P2 |
| FR-ADM-09 | Impersonation / "view as" for support, logged in the audit trail. | P2 |

### 5.3 Teacher Portal — Content Ingestion

| ID | Requirement | Priority |
|---|---|---|
| FR-ING-01 | Upload one or more PDFs (drag-and-drop + file picker) to a selected course. Max 50 MB/file, max 20 files/batch. | P0 |
| FR-ING-02 | Validate MIME type and magic bytes; reject encrypted or corrupt PDFs with a clear error. | P0 |
| FR-ING-03 | Persist the original file and a checksum; duplicate checksum in the same course warns the user. | P1 |
| FR-ING-04 | Asynchronous ingestion job with statuses: `queued → extracting → chunking → embedding → topic_extraction → ready` (or `failed` with reason). | P0 |
| FR-ING-05 | Progress is visible in the UI; failed jobs can be retried without re-uploading. | P0 |
| FR-ING-06 | Text extraction preserves page numbers and heading structure; pages with no text layer are flagged as "image-only" (OCR is P2). | P0 |
| FR-ING-07 | Chunking is structure-aware: split on headings/sections where detectable, then by token window (~600 tokens, ~15% overlap), never mid-sentence. | P0 |
| FR-ING-08 | Every chunk stores: `document_id`, `course_id`, `page_start`, `page_end`, `section_title`, `chunk_index`, `token_count`, `text`, `embedding_id`. | P0 |
| FR-ING-09 | Embeddings generated with `all-MiniLM-L6-v2` (384-dim, normalized), batched for throughput. No external API key required. | P0 |
| FR-ING-10 | Vectors persisted in ChromaDB with metadata enabling course/topic/document filtering. | P0 |
| FR-ING-11 | Re-ingesting a document replaces its chunks atomically (no duplicate or orphaned vectors). | P1 |
| FR-ING-12 | Ingestion is idempotent per (document checksum, pipeline version). | P1 |

### 5.4 Teacher Portal — Topic Extraction & Selective Release

| ID | Requirement | Priority |
|---|---|---|
| FR-TOP-01 | After ingestion, the system generates a structured topic list from the corpus: title, 1–2 sentence summary, page range, chunk IDs, estimated reading time. | P0 |
| FR-TOP-02 | Topic extraction uses an LLM (Groq) over chunk summaries/outlines, with strict JSON schema output and retry/repair on invalid JSON. | P0 |
| FR-TOP-03 | Topic extraction must degrade gracefully: if the LLM is unavailable, fall back to heading-based topic detection and flag it for review. | P0 |
| FR-TOP-04 | Teacher can rename, edit summary, merge, split, reorder, and delete proposed topics. | P0 |
| FR-TOP-05 | Every topic defaults to `unreleased`; only an explicit release action changes state. | P0 |
| FR-TOP-06 | Teacher can release/unrelease a topic; unrelease hides it and its tasks/quizzes from students but preserves student attempt history. | P0 |
| FR-TOP-07 | Release action captures `released_by` and `released_at` and writes an audit entry. | P0 |
| FR-TOP-08 | On release, the system creates the corresponding student task (title, summary, due date, week) and optionally a quiz. | P0 |
| FR-TOP-09 | Teacher can schedule a release for a future date/time; scheduled releases are applied by a background job. | P2 |
| FR-TOP-10 | Teacher can preview the student view of any released or unreleased topic. | P1 |

### 5.5 Student Portal

| ID | Requirement | Priority |
|---|---|---|
| FR-STU-01 | Dashboard shows institution/instructor announcements, newest first, with unread indicator. | P0 |
| FR-STU-02 | Announcements support title, rich-text body, optional attachment/link, publish window, and target cohort. | P0 |
| FR-STU-03 | "Your Tasks" lists only tasks from released topics in courses the student is enrolled in. | P0 |
| FR-STU-04 | Tasks filter by status: **All**, **Incomplete**, **Completed**; sort by due date, release date, or course. | P0 |
| FR-STU-05 | Task card shows title, course, week, due date, status badge, and completion state. Overdue incomplete tasks are visually distinct. | P0 |
| FR-STU-06 | Student can open a task to view its summary, linked source pages/excerpt, and mark it complete/incomplete. Completion is timestamped. | P0 |
| FR-STU-07 | Student can ask a natural-language question about released material ("study assistant") and receive an answer grounded in retrieved chunks with page citations. | P1 |
| FR-STU-08 | Study assistant must refuse or state "not covered in released material" when retrieval confidence is low; it must never answer from unreleased content. | P0 |
| FR-STU-09 | Test section lists quizzes for released topics, showing availability, attempt status, and best score. | P0 |
| FR-STU-10 | Quiz UI: one question at a time or paginated, timer optional, progress indicator, submit confirmation, auto-save on navigation. | P0 |
| FR-STU-11 | Results view shows score, per-question correctness, correct answer, short explanation, and source page reference. | P0 |
| FR-STU-12 | Attempt history is retained and visible to the student. | P1 |
| FR-STU-13 | Student sees personal progress: tasks completed vs. assigned, quiz average, streak/days active. | P1 |

### 5.6 Assessment (Quiz) Requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-QUIZ-01 | Quizzes are generated from the chunks of a released topic only. | P0 |
| FR-QUIZ-02 | Each generated question includes stem, 4 options (or short answer), correct answer, explanation, difficulty, and source chunk/page. | P0 |
| FR-QUIZ-03 | Generation uses Groq with strict JSON schema; invalid or ungrounded questions are discarded and regenerated. | P0 |
| FR-QUIZ-04 | Teacher reviews/edits/approves generated questions before students see them (configurable to auto-approve). | P1 |
| FR-QUIZ-05 | Configurable quiz size (default 10), pass threshold (default 60%), and attempt limit (default unlimited, tracked). | P1 |
| FR-QUIZ-06 | Grading is server-side; correct answers are never sent to the client before submission. | P0 |
| FR-QUIZ-07 | Answers are stored per attempt for knowledge-gap analytics. | P0 |

### 5.7 Teacher Analytics & Dashboards

| ID | Requirement | Priority |
|---|---|---|
| FR-ANA-01 | Cohort dashboard: total students, overall completion rate, pending vs. completed tasks, average quiz score. | P0 |
| FR-ANA-02 | Per-student row: name, completion %, tasks completed/pending, last active timestamp, days active, average score. | P0 |
| FR-ANA-03 | Per-topic view: release state, completion rate, average score, question-level accuracy. | P0 |
| FR-ANA-04 | Knowledge-gap detection: rank topics/questions by lowest accuracy and flag students below threshold. | P1 |
| FR-ANA-05 | Attempt history drill-down: date, quiz, score, duration, incorrect questions with source references. | P1 |
| FR-ANA-06 | Filters by course, week/date range, and student; exportable CSV. | P2 |
| FR-ANA-07 | Engagement definition is explicit: `last_active_at` = last authenticated request or learning action; `days_active` = distinct UTC dates with at least one learning action. | P0 |

### 5.8 Notifications

| ID | Requirement | Priority |
|---|---|---|
| FR-NOT-01 | In-app notification when a new task/quiz is released to a student's cohort. | P1 |
| FR-NOT-02 | In-app notification to teacher when ingestion completes or fails. | P1 |
| FR-NOT-03 | Optional email digest for announcements and due-date reminders. | P2 |

---

## 6. RAG Architecture

### 6.1 Pipeline

```
PDF upload
   │
   ▼
[1] Validate ──► [2] Extract text (+page map, headings)
                        │
                        ▼
                 [3] Structure-aware chunking
                        │
                        ▼
                 [4] Embed (all-MiniLM-L6-v2, 384d, normalized)
                        │
                        ▼
                 [5] Upsert into ChromaDB (metadata: course, document, topic, page)
                        │
                        ▼
                 [6] Topic extraction (Groq, JSON schema, chunk→topic map)
                        │
                        ▼
                 [7] Teacher review  ──►  [8] Release gate
                                                  │
                                                  ▼
                                   [9] Student retrieval (filter: released = true)
                                                  │
                                                  ▼
                                   [10] Groq generation + citations
```

### 6.2 Component Specification

| Stage | Implementation | Notes |
|---|---|---|
| Load | `pypdf` / `PyMuPDF`; keep per-page text and detected headings | Encrypted/corrupt files rejected at upload |
| Chunk | Heading-aware split, then token window ~600 tokens / ~90 overlap | Store page span and section title |
| Embed | `sentence-transformers/all-MiniLM-L6-v2`, `normalize_embeddings=True`, batch 32–64 | 384 dims; CPU-friendly; model cached locally |
| Store | ChromaDB persistent client, one collection (or per-course collection) with metadata filters | `cosine` distance |
| Retrieve | Top-k (default 6) with `where` filters: `course_id`, `released = true`, optional `topic_id` | MMR or score threshold to improve diversity |
| Generate | Groq chat completion, temperature 0.1–0.3, max tokens bounded | Prompt includes numbered context blocks with page refs |
| Ground | Answer must cite `[doc, page]`; if max retrieval score < threshold, return "not covered" | Prevents hallucinated curriculum |

### 6.3 Retrieval & Release-Gate Invariant

The release gate is enforced **at query time, not only in the UI**:

- Student-facing queries always include the Chroma filter `released = true` **and** `course_id ∈ student's enrolled courses`.
- Topic records carry `release_state`; chunk metadata carries a denormalized `released` flag that is updated when a topic is released/unreleased. Re-indexing is not required for a release toggle.
- Unreleased-topic tasks and quizzes are filtered out at the service layer, not by hiding them in the client.
- A regression test asserts that a student query cannot retrieve a chunk from an unreleased topic.

### 6.4 Topic Extraction Contract (JSON)

```json
{
  "topics": [
    {
      "title": "string (3-90 chars)",
      "summary": "string (<= 300 chars)",
      "page_start": 12,
      "page_end": 27,
      "chunk_ids": ["doc123:c004", "doc123:c005"],
      "estimated_minutes": 25
    }
  ]
}
```

Validation: unique titles, valid page ranges within the document, every topic maps to ≥ 1 existing chunk, total chunk coverage ≥ 90% (unmapped chunks are attached to the nearest topic and flagged).

### 6.5 Prompt & Safety Rules

- System prompt: "Answer only from the provided context. If the context is insufficient, say so. Cite page numbers."
- Context blocks are delimited and numbered; user input is never concatenated into the system prompt.
- Output length capped; no markdown injection into UI (render safely).
- Rate limit per user (e.g., 20 RAG queries/hour for students) and global budget guard for Groq calls.
- Log prompt version, retrieved chunk IDs, latency, token usage, and refusal flag — never log full student PII into LLM logs.

### 6.6 Environment Configuration

```dotenv
# LLM
GROQ_API_KEY=...
GROQ_MODEL=openai/gpt-oss-120b
GROQ_MAX_TOKENS=1024

# Embeddings / vectors
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_DIM=384
CHROMA_PERSIST_DIR=./data/chroma
CHROMA_COLLECTION=lms_chunks

# App
DATABASE_URL=...
SESSION_SECRET=...
UPLOAD_DIR=./data/uploads
MAX_UPLOAD_MB=50
```

Secrets live only in `.env` (git-ignored); `.env.example` is committed with placeholders. Startup fails fast if a required key is missing.

---

## 7. Data Model (Logical)

| Entity | Key fields |
|---|---|
| `users` | id, name, email (unique), password_hash, role, status, created_at, last_active_at |
| `courses` | id, title, code, description, created_by, status |
| `enrollments` | id, course_id, user_id, role_in_course, enrolled_at |
| `documents` | id, course_id, uploaded_by, filename, checksum, size_bytes, page_count, status, error, pipeline_version, created_at |
| `topics` | id, document_id, course_id, title, summary, page_start, page_end, order_index, release_state, released_by, released_at |
| `chunks` | id (vector id), document_id, topic_id, course_id, page_start, page_end, section_title, text, token_count, released |
| `tasks` | id, topic_id, course_id, title, description, week_no, due_at, created_at |
| `task_progress` | id, task_id, student_id, status (`completed`/`incomplete`), completed_at |
| `quizzes` | id, topic_id, course_id, title, pass_threshold, attempt_limit, status |
| `questions` | id, quiz_id, stem, options (JSON), correct_option, explanation, difficulty, source_chunk_id, approved |
| `quiz_attempts` | id, quiz_id, student_id, started_at, submitted_at, score, max_score |
| `attempt_answers` | id, attempt_id, question_id, selected_option, is_correct, time_spent_ms |
| `announcements` | id, course_id (nullable = institution-wide), author_id, title, body, publish_at, expires_at |
| `notifications` | id, user_id, type, payload, read_at, created_at |
| `audit_logs` | id, actor_id, action, entity_type, entity_id, before/after (JSON), ip, created_at |
| `ingestion_jobs` | id, document_id, stage, progress_pct, attempts, started_at, finished_at, error |

Indexes: `users.email`, `enrollments(course_id, user_id)`, `chunks(course_id, released)`, `tasks(course_id, due_at)`, `task_progress(student_id, status)`, `quiz_attempts(student_id, quiz_id)`.

---

## 8. API Surface (Indicative)

| Method | Path | Role | Purpose |
|---|---|---|---|
| POST | `/api/auth/login` | public | Authenticate |
| POST | `/api/auth/logout` | any | End session |
| GET | `/api/me` | any | Current user + role |
| GET/POST | `/api/admin/users` | admin | List/create users |
| PATCH/DELETE | `/api/admin/users/{id}` | admin | Update/deactivate/delete |
| GET/POST | `/api/courses` | teacher/admin | List/create courses |
| POST | `/api/courses/{id}/enrollments` | admin/teacher | Enroll students |
| POST | `/api/documents` | teacher/admin | Upload PDF (multipart) |
| GET | `/api/documents/{id}/status` | teacher/admin | Ingestion progress |
| POST | `/api/documents/{id}/retry` | teacher/admin | Retry failed ingestion |
| GET | `/api/documents/{id}/topics` | teacher/admin | Proposed topics |
| PATCH | `/api/topics/{id}` | teacher/admin | Edit topic |
| POST | `/api/topics/{id}/release` | teacher/admin | Release (creates task/quiz) |
| POST | `/api/topics/{id}/unrelease` | teacher/admin | Revoke student visibility |
| GET | `/api/student/tasks?status=` | student | Filtered task list |
| PATCH | `/api/student/tasks/{id}` | student | Mark complete/incomplete |
| GET | `/api/student/announcements` | student | Announcement feed |
| GET | `/api/student/quizzes` | student | Available quizzes |
| POST | `/api/student/quizzes/{id}/attempts` | student | Start attempt |
| POST | `/api/student/attempts/{id}/submit` | student | Submit answers, get result |
| POST | `/api/student/ask` | student | RAG study assistant (released-only) |
| GET | `/api/teacher/analytics?course_id=` | teacher/admin | Cohort analytics |
| GET | `/api/teacher/analytics/students/{id}` | teacher/admin | Student drill-down |
| GET/POST | `/api/announcements` | teacher/admin | Manage announcements |
| GET | `/api/admin/audit-logs` | admin | Audit trail |

Conventions: JSON, RFC 7807 problem details for errors, cursor pagination on lists, idempotency keys on upload/release where retries are expected.

---

## 9. UX Requirements

### 9.1 Shell & Navigation

- Three-column-capable responsive layout; sidebar navigation scoped to the active portal.
- Role switcher is **not** a UI feature for normal users; admins get a clearly labeled "View as" mode.
- Persistent portal header showing user, role, course selector (where relevant), and notifications.
- Empty states are instructive ("No tasks released yet — check back after your instructor publishes this week's topics").
- Loading skeletons for ingestion and analytics; no blocking spinners over 2 seconds without progress text.

### 9.2 Key Screens

1. **Login** — email/password, error states, redirect by role.
2. **Admin: Users** — table with search, role filter, bulk actions, create/edit drawer.
3. **Admin: Content Repository** — documents with ingestion status, topic counts, release coverage.
4. **Teacher: Upload & Ingest** — dropzone, per-file progress, stage labels, retry.
5. **Teacher: Topic Review** — extracted topic cards, inline edit, merge/split, page-range badges, release toggle, bulk release.
6. **Teacher: Analytics** — KPI cards, student table, topic heatmap, drill-down panel.
7. **Student: Home** — announcements feed + task summary + progress strip.
8. **Student: Tasks** — All/Incomplete/Completed tabs, due-date sort, status badges.
9. **Student: Test** — quiz list, quiz runner, results with citations.
10. **Student: Study Assistant** — chat with cited answers and a visible "based on released material" disclaimer.

### 9.3 Accessibility

- WCAG 2.1 AA: keyboard navigation, visible focus, ≥ 4.5:1 contrast, semantic headings, ARIA labels on status badges and filters.
- Status is never conveyed by color alone (badge text + icon).

---

## 10. Non-Functional Requirements

| Category | Requirement |
|---|---|
| **Performance** | P95 API latency < 400 ms for CRUD; RAG answer P95 < 8 s; dashboard P95 < 1.5 s for 500 students. |
| **Ingestion throughput** | 200-page PDF end-to-end < 5 min on a 4-vCPU worker; embedding batches run off the request thread. |
| **Scale (v1)** | 5,000 users, 200 courses, 2,000 documents, 1M chunks per deployment. |
| **Availability** | 99.5% monthly for the web app; ingestion degrades gracefully if Groq is down (topics fall back to headings). |
| **Security** | OWASP Top 10 controls; parameterized queries; signed uploads; file-type validation; per-role authorization tests; secrets never in the repo. |
| **Privacy** | Student data minimized; LLM prompts exclude emails/IDs; configurable retention for attempts and logs; admin deletion honored across DB and vector store. |
| **Auditability** | Privileged actions logged with actor, timestamp, and before/after diff. |
| **Observability** | Structured logs with request IDs; metrics for ingestion duration, failure rate, retrieval hit rate, refusal rate, LLM latency/tokens. |
| **Portability** | Local dev runs with Docker Compose (app + DB + Chroma volume); no cloud-only dependency except Groq API. |
| **Reliability** | Ingestion jobs retry up to 3 times with backoff; partial failures never leave a document in a half-released state. |
| **Cost control** | Per-user RAG rate limits; prompt/response token caps; embedding is local and free. |

---

## 11. Technical Stack (Recommended)

| Layer | Choice | Rationale |
|---|---|---|
| Frontend | Next.js (App Router) + TypeScript + Tailwind + shadcn/ui | Fast portal scaffolding, SSR auth guards, one codebase for three portals |
| Backend | Next.js route handlers or FastAPI service | Python preferred for the RAG worker (`sentence-transformers`, `pypdf`) |
| Database | PostgreSQL (Neon-compatible) | Relational integrity for enrollments, attempts, analytics |
| ORM | Prisma or SQLAlchemy | Typed schema + migrations |
| Vector DB | ChromaDB (persistent) | Required by scope; simple metadata filtering |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` | Required; local, 384-dim, no API key |
| LLM | Groq API | Required; low-latency generation for topics, quizzes, answers |
| Job runner | In-process queue (v1) → Celery/RQ + Redis (scale) | Async ingestion without blocking requests |
| Auth | Auth.js / custom session cookies | Role-aware, server-enforced |
| Testing | Vitest/Jest + Playwright + pytest | Unit, e2e, and RAG regression tests |

---

## 12. Delivery Plan

### Phase 0 — Foundation (Week 1)

- Repo scaffolding, env config, DB schema + migrations, seed data.
- Auth + role guards; base layout and navigation shell.
- Docker Compose for Postgres + Chroma.

### Phase 1 — Unified Portal Skeleton (Weeks 2–3)

- All three portal views rendered in one interface (per delivery strategy).
- Admin user/course CRUD.
- Announcements CRUD + student feed.
- Static task and quiz screens with mock data.

### Phase 2 — RAG Ingestion & Topic Review (Weeks 3–5)

- Upload, validation, async pipeline, status UI, retry.
- Chunking + embeddings + Chroma persistence.
- Topic extraction (Groq) with JSON validation and heading fallback.
- Topic edit/merge/split and release toggle.

### Phase 3 — Student Execution (Weeks 5–7)

- Released-topic → task generation and student task list with filters.
- Quiz generation, teacher review, quiz runner, grading, results with citations.
- Student study assistant with release-gated retrieval.

### Phase 4 — Analytics & Admin Oversight (Weeks 7–8)

- Cohort/student/topic analytics, knowledge-gap ranking, CSV export.
- Audit logs, permission configuration, content repository management.

### Phase 5 — Segregation & Hardening (Weeks 8–10)

- Split unified interface into role-scoped portals with route guards.
- Performance tuning, accessibility pass, security review.
- RAG evaluation set, prompt tuning, regression tests, documentation.

---

## 13. Acceptance Criteria (Definition of Done)

A release is accepted when all of the following hold:

1. A teacher uploads a PDF and sees it reach `ready` with a topic list; a deliberately corrupted PDF fails with a human-readable error and can be retried.
2. Editing a topic changes the student-visible task title after release; unreleased topics produce zero student-visible tasks, quizzes, or retrieval hits.
3. A student sees only tasks from their enrolled courses, can filter All/Incomplete/Completed, and completion persists across sessions.
4. A quiz generated from a released topic has server-side grading, cited source pages, and records per-question results.
5. A student question about unreleased material returns "not covered in released material," not an answer.
6. Teacher analytics show correct completion %, last-active timestamps, and per-topic accuracy for seeded data.
7. Admin can create a user, change a role, deactivate an account, and see each action in the audit log.
8. Automated tests cover the release-gate invariant, RBAC on every mutating endpoint, and the ingestion pipeline on a sample PDF.
9. `README` documents setup, `.env.example`, seeding, and sample Q&A; a clean clone runs locally via Docker Compose.

---

## 14. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| LLM hallucinates content not in the corpus | Wrong teaching material, trust loss | Strict grounding prompt, retrieval score threshold, citations required, refusal path, teacher review before release |
| Topic extraction produces poor segmentation | Teachers abandon the feature | Heading fallback, editable/mergeable topics, confidence flags, evaluate on the sample NCERT corpus |
| Release gate bypass via API | Content leak | Enforce in service layer + vector filter; regression tests; audit releases |
| Slow ingestion on large PDFs | Poor first-run experience | Async jobs, batch embeddings, progress UI, page-count guardrails |
| ChromaDB scaling limits | Slow retrieval at scale | Per-course collections, metadata pre-filtering, plan migration path to a managed vector DB |
| Groq outage or rate limits | Feature unavailable | Fallbacks (heading topics, pre-generated quizzes), bounded retries, queueing |
| PII sent to LLM | Privacy/compliance issue | Strip identifiers from prompts, log only chunk IDs, data-processing note |
| Unified UI blurs role boundaries | Security gaps in Phase 1 | Server-side guards from day one; segregation is a UI change, not an auth change |

---

## 15. Open Questions

1. ~~**Cohort model:** Is a student enrolled in one course or many concurrently?~~ **Resolved:** students may be enrolled in many courses concurrently (many-to-many).
2. **Weekly cadence:** Are tasks strictly week-numbered, or date-range based with teacher-defined due dates?
3. ~~**Quiz format:** Multiple-choice only in v1, or short-answer with LLM-assisted grading?~~ **Resolved:** MCQ-only in v1 (4 options, one correct, deterministic server-side grading).
4. **Institution branding:** Is per-institution theming needed in v1 or deferred?
5. **Retention:** How long should quiz attempts and RAG chat logs be retained?
6. **Email:** Is SMTP available for password reset and digests, or is in-app only acceptable?
7. **Deployment target:** Single institution self-hosted, or a shared cloud deployment?
8. **Sample corpus:** The repository currently contains NCERT PDFs (`hesc1xx`, `hess2xx`, `hess3xx`). Confirm these are the intended seed corpus and licensing for redistribution.

---

## 16. Appendix

### 16.1 Sample Q&A (for evaluation)

| Context | Question | Expected behavior |
|---|---|---|
| Released chapter on chemical reactions | "Why does a shiny brown object turn green on exposure to air?" | Answer from chapter text with page citation |
| Released chapter only | "Explain the topic from chapter 12" (chapter 12 unreleased) | Refuse: not covered in released material |
| Two released chapters | "Compare X and Y" | Answer using both, cite both sources |
| Any | "What is the teacher's email?" | Refuse: out of scope for the study assistant |

### 16.2 Seed Data

- 1 admin, 3 teachers, 30 students, 2 courses, 2 cohorts.
- 3 seeded PDFs from the repository root, ingested and pre-released for demo.
- 1 unreleased document to demonstrate the gate.
- 5 announcements, 4 released topics, 2 quizzes with 10 questions each, 20 simulated attempts.

### 16.3 Glossary

| Term | Meaning |
|---|---|
| RAG | Retrieval-Augmented Generation — retrieve relevant chunks, then generate an answer grounded in them |
| Chunk | A passage of a document stored with its embedding and metadata |
| Topic | A teacher-reviewable unit of content extracted from one or more chunks |
| Release | Explicit teacher action making a topic and its derived tasks/quizzes visible to students |
| Grounding | Constraining generation to retrieved source text, with citations |
| Knowledge gap | A topic/question where cohort accuracy is materially below threshold |
