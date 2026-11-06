# Architecture Document

## RAG-Powered Multi-Portal Learning Management System

| Field | Value |
|---|---|
| **Companion document** | `prd.md` (v1.0) |
| **Document version** | 1.0 |
| **Status** | Proposed |
| **Last updated** | 2026-10-30 |
| **Audience** | Engineering, DevOps, QA, Security |

---

## 1. Scope

This document describes the technical architecture that satisfies `prd.md`: a three-portal LMS (Student, Teacher, Admin) with a PDF-to-RAG ingestion pipeline, release-gated content, MCQ assessments, and cohort analytics.

It covers system decomposition, data and vector storage, the RAG pipeline, request/async flows, security boundaries, deployment, and the decisions behind them. It does **not** restate product requirements; requirement IDs (`FR-*`) reference the PRD.

### 1.1 Resolved product decisions

| Decision | Resolution | Architectural impact |
|---|---|---|
| Student course membership | **Many-to-many** — a student may be enrolled in multiple courses concurrently | `enrollments` join table; every student query filters on a course-ID set; task/quiz lists aggregate across courses |
| Quiz format (v1) | **MCQ only** — 4 options, exactly one correct | `questions.options` JSON + `correct_option` index; no rubric/free-text grading service; deterministic server-side grading |
| Cohort vs. course | "Cohort" in the PRD maps to a `courses` row in v1 (a course *is* an offering/section) | No separate `cohorts` table; additive migration path documented in §8.2 |
| Portal rollout | Unified UI first, role-scoped routes second | Authorization is server-side from day one; Phase 9 (hardening) is a routing/UI change only |

---

## 2. Architecture Principles

1. **One authoritative API.** All business rules live in the backend service. The frontend never decides visibility, release state, or grading.
2. **Release gating is defense-in-depth.** Enforced independently at the service layer, the SQL query layer, and the vector-store filter. Any one layer failing must not expose content.
3. **Grounded generation.** The LLM is a renderer over retrieved context, not a knowledge source. Every AI output is traceable to chunk IDs.
4. **Async by default for slow work.** PDF parsing, embedding, topic extraction, and quiz generation run in workers; HTTP requests stay bounded.
5. **Postgres is the source of truth.** Chroma is a derived, rebuildable index. Deleting or rebuilding the vector store must never lose product data.
6. **Idempotent, resumable pipelines.** Re-running an ingestion job for the same document checksum converges to the same state.
7. **Boring, replaceable infrastructure.** Chroma, Groq, and the embedding model sit behind adapter interfaces so they can be swapped without touching domain code.

---

## 3. Quality Attributes

| Attribute | Target | Architectural mechanism |
|---|---|---|
| Correctness of visibility | Zero unreleased leaks | Triple-layer release gate + regression suite (§9.3, §13) |
| Latency | CRUD P95 < 400 ms; RAG answer P95 < 8 s | Indexed queries, async ingestion, bounded retrieval, streaming answers |
| Ingestion throughput | 200 pages < 5 min | Batched embeddings, worker concurrency, CPU-optimized MiniLM |
| Scale (v1) | 5k users, 2k documents, 1M chunks | Single Postgres + single Chroma node is sufficient (§14) |
| Availability | 99.5% app; graceful AI degradation | Heading-based topic fallback, cached quiz bank, health checks |
| Security | OWASP Top 10; least privilege | Server-side RBAC, scoped repositories, upload hardening, prompt isolation |
| Operability | One-command local stack | Docker Compose: web, api, worker, postgres, redis, chroma |
| Auditability | Every privileged action recorded | Append-only `audit_logs`, actor + diff + request ID |

---

## 4. System Context (C4 Level 1)

```mermaid
flowchart LR
    Student["Student<br/>(web browser)"]
    Teacher["Teacher<br/>(web browser)"]
    Admin["Administrator<br/>(web browser)"]

    LMS["LMS Platform<br/>Multi-portal app + RAG"]

    Groq["Groq API<br/>LLM inference"]
    Mail["SMTP provider<br/>(optional)"]
    Store["Object storage<br/>PDF originals"]

    Student -->|"HTTPS"| LMS
    Teacher -->|"HTTPS"| LMS
    Admin -->|"HTTPS"| LMS

    LMS -->|"Chat completions"| Groq
    LMS -->|"Password reset / digests"| Mail
    LMS -->|"Signed read/write"| Store
```

**Trust boundaries**

- Browser ↔ LMS: untrusted input, authenticated session required for everything except login/reset.
- LMS ↔ Groq: content egress boundary. Only chunk text and the user question leave the system; no PII.
- LMS ↔ object storage: PDFs are private; access only via short-lived signed URLs.

---

## 5. Container View (C4 Level 2)

```mermaid
flowchart TB
    subgraph Client
        Browser["Browser<br/>Next.js SPA/SSR"]
    end

    subgraph Platform["Platform (private network)"]
        Web["web<br/>Next.js 15 + TypeScript<br/>SSR shell, route guards (UX only)"]
        API["api<br/>FastAPI<br/>Auth, domain logic, RAG query, analytics"]
        Worker["worker<br/>Python (ARQ)<br/>Ingest, embed, topics, quizzes"]
        DB[("PostgreSQL 16<br/>source of truth")]
        Redis[("Redis 7<br/>job queue + cache")]
        Chroma[("ChromaDB server<br/>vector index")]
        Files[("Object storage / volume<br/>PDF originals")]
    end

    Groq["Groq API"]

    Browser -->|"HTTPS"| Web
    Web -->|"HTTPS (server-side fetch, cookie forward)"| API
    Browser -->|"HTTPS (direct API calls)"| API
    API --> DB
    API --> Redis
    API --> Chroma
    API -->|"signed URLs"| Files
    API -->|"generation"| Groq
    Worker --> DB
    Worker --> Redis
    Worker --> Chroma
    Worker -->|"read PDF"| Files
    Worker -->|"topics, quizzes"| Groq
```

### 5.1 Container responsibilities

| Container | Responsibility | Explicitly not responsible for |
|---|---|---|
| `web` (Next.js) | Rendering, routing, client state, optimistic UI, calling the API | Authorization decisions, data filtering, grading |
| `api` (FastAPI) | AuthN/AuthZ, CRUD, release workflow, task/quiz assembly, retrieval + generation for student queries, analytics queries | Long-running ingestion (delegated to worker) |
| `worker` (ARQ) | PDF extraction, chunking, embedding, Chroma upsert, topic extraction, quiz generation, scheduled releases, notification fan-out | Serving HTTP requests |
| PostgreSQL | Users, courses, enrollments, documents, topics, chunks metadata, tasks, quizzes, attempts, announcements, audit, job state | Vector search |
| ChromaDB (server mode) | Chunk embeddings + filterable metadata | Being the source of truth for release state |
| Redis | ARQ broker, rate-limit counters, short-lived caches, distributed locks | Durable state (job state lives in Postgres) |
| Object storage | Original PDFs, exported CSVs, announcement attachments | Being publicly readable |

**Why Chroma runs as a server, not embedded:** the API and worker are separate processes. An embedded `PersistentClient` does not support safe multi-process concurrent writes. Running `chroma run` and connecting both processes over HTTP gives one writer-of-record, consistent reads, and a clean path to a managed vector DB later.

---

## 6. Component Architecture

### 6.1 Backend (`api` + `worker` share one Python package)

```
lms/
├── api/                      # FastAPI app (HTTP layer only)
│   ├── routers/
│   │   ├── auth.py
│   │   ├── admin_users.py
│   │   ├── courses.py
│   │   ├── documents.py
│   │   ├── topics.py
│   │   ├── student_tasks.py
│   │   ├── student_quiz.py
│   │   ├── student_assistant.py
│   │   ├── announcements.py
│   │   └── analytics.py
│   ├── deps.py               # current_user, require_role, db session, pagination
│   ├── middleware.py         # request ID, logging, rate limit, error envelope
│   └── schemas/              # Pydantic request/response models
├── services/                 # Business logic; the only place rules live
│   ├── auth_service.py
│   ├── user_service.py
│   ├── course_service.py
│   ├── document_service.py
│   ├── topic_service.py      # edit/merge/split + release gate
│   ├── task_service.py
│   ├── quiz_service.py       # quiz review, generation orchestration, publishing
│   ├── attempt_service.py    # student start/autosave/submit/grading/history
│   ├── analytics_service.py
│   ├── announcement_service.py
│   └── audit_service.py
├── rag/
│   ├── extract.py            # PDF → pages + headings
│   ├── chunker.py            # structure-aware, token window
│   ├── embedder.py           # MiniLM adapter (lazy singleton)
│   ├── vector_store.py       # Chroma adapter (upsert/query/update/get/delete)
│   ├── access.py             # student_vector_where: course scope + released=true
│   ├── retriever.py          # filters, top-k, MMR, threshold
│   ├── generator.py          # Groq adapter, prompt assembly, citations
│   ├── topics.py             # topic extraction + heading fallback
│   └── quizzes.py            # MCQ generation + validation
├── workers/
│   ├── settings.py           # ARQ WorkerSettings
│   ├── ingest.py             # ingest_document job
│   ├── generate_quiz.py      # quiz generation job
│   └── scheduled.py          # cron: scheduled releases, due reminders
├── db/
│   ├── models.py             # SQLAlchemy 2.0 models
│   ├── repositories/         # query objects, always tenant/course-scoped
│   └── migrations/           # Alembic
└── core/
    ├── config.py             # pydantic-settings, fail-fast env validation
    ├── security.py           # Argon2, JWT, CSRF
    └── logging.py            # structlog config
```

**Layering rule:** `router → service → repository → model`. Routers contain no SQL and no business rules. Services never build SQL strings. Repositories never make authorization decisions, but they always accept an explicit scope (`course_ids`, `student_id`) so a forgotten filter is a type error rather than a silent leak.

### 6.2 Frontend (`web`)

```
web/
├── app/
│   ├── (auth)/login, reset-password
│   ├── (student)/dashboard, tasks, tests/[quizId], assistant
│   ├── (teacher)/documents, documents/[id]/topics, analytics, announcements
│   └── (admin)/users, courses, content, permissions, audit
├── components/
│   ├── ui/                   # shadcn/ui primitives
│   ├── tasks/                # TaskCard, StatusBadge, FilterTabs
│   ├── topics/               # TopicCard, ReleaseToggle, MergeDialog
│   └── charts/               # analytics visualizations
├── lib/
│   ├── api.ts                # typed fetch wrapper, error normalization
│   ├── auth.ts               # session probe for routing only
│   └── queries/              # TanStack Query hooks
└── middleware.ts             # UX redirects only — never the security boundary
```

Frontend guard rails:

- Route middleware checks a non-sensitive role claim to redirect users to their portal home. It is **not** an authorization control; every API call re-checks.
- Server Components fetch through the API with the user's cookies forwarded, so no privileged data is fetched at build time.
- No secrets, no Groq calls, no vector access in the browser.

---

## 7. Technology Stack

| Layer | Choice | Rationale / constraint |
|---|---|---|
| UI | Next.js 15 (App Router), TypeScript, Tailwind, shadcn/ui | Fast multi-portal scaffolding, SSR, good a11y primitives |
| API | FastAPI + Pydantic v2 | Python-native RAG ecosystem, schema-first contracts |
| ORM / migrations | SQLAlchemy 2.0 + Alembic | Typed models, reversible migrations |
| DB | PostgreSQL 16 | Relational integrity for enrollments/attempts/analytics |
| Vector DB | ChromaDB (server mode) | Required by scope; metadata filtering |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (384-d, normalized) | Required; local, CPU-friendly, no API key |
| LLM | Groq (`openai/gpt-oss-120b` default, configurable) | Required; low latency |
| Queue | ARQ + Redis | Async-native, minimal ops; Celery is a drop-in alternative |
| PDF parsing | PyMuPDF (primary), pypdf (fallback) | Page-accurate text + heading detection |
| Auth | Argon2id + JWT (access 15 min / refresh 7 days) in `httpOnly` cookies | Stateless verification, revocable refresh |
| Observability | structlog + Prometheus metrics + OpenTelemetry traces | Correlated logs across API → worker → LLM |
| Testing | pytest, testcontainers, Playwright, Vitest | Unit → integration → e2e → RAG eval |

---

## 8. Data Architecture

### 8.1 Entity relationship

```mermaid
erDiagram
    users ||--o{ enrollments : "has"
    courses ||--o{ enrollments : "has"
    users ||--o{ documents : "uploads"
    courses ||--o{ documents : "contains"
    documents ||--o{ topics : "yields"
    topics ||--o{ chunks : "groups"
    topics ||--o{ tasks : "releases as"
    topics ||--o{ quizzes : "releases as"
    users ||--o{ task_progress : "completes"
    tasks ||--o{ task_progress : "tracked by"
    quizzes ||--o{ questions : "contains"
    users ||--o{ quiz_attempts : "takes"
    quizzes ||--o{ quiz_attempts : "receives"
    quiz_attempts ||--o{ attempt_answers : "records"
    questions ||--o{ attempt_answers : "answered in"
    users ||--o{ announcements : "authors"
    courses ||--o{ announcements : "targets"
    users ||--o{ audit_logs : "acts"
```

### 8.2 Key tables and design notes

| Table | Design notes |
|---|---|
| `users` | `role` enum (`student`, `teacher`, `admin`); `status` (`active`, `inactive`); unique lowercase email; `last_active_at` updated at most once per 5 minutes to avoid write amplification |
| `courses` | v1 offering/section: `code` (unique), `term`, `created_by`, `status`. Add `cohorts` later as `courses.parent_course_id` without breaking enrollments |
| `enrollments` | PK `(course_id, user_id)`; `course_role` (`student`, `teacher`); unique per pair. Many-to-many by design (§1.1) |
| `documents` | `checksum` unique per course; `status` mirrors job state; `pipeline_version`; `page_count`; `error_code`, `error_detail` |
| `topics` | `release_state` (`draft`, `released`, `scheduled`); `order_index`; `page_start/end`; `released_by/at`; soft delete via `deleted_at` |
| `chunks` | Metadata mirror of Chroma; `vector_id` = `"{document_id}:{chunk_index}"`; `released` denormalized from topic; `text` retained for rebuilds and citations |
| `tasks` | One per (topic, course); `week_no`, `due_at`; `status` (`draft`, `published`, `archived`); created inside the release transaction |
| `task_progress` | PK `(task_id, student_id)`; `status`; `completed_at`; unique to prevent duplicate completion rows |
| `quizzes` | One per topic (`topic_id` unique); `pass_threshold`, `attempt_limit` (0 = unlimited), `status` (`draft`, `published`, `archived`), `auto_approve`, `generation_status` (`queued`, `generating`, `ready`, `failed`), `generation_error` |
| `questions` | MCQ only: `options` JSONB array of 4 strings, `correct_option` smallint 0–3, `explanation`, `difficulty`, `source_chunk_id`, `approved` |
| `quiz_attempts` | `score`, `max_score`, `started_at`, `submitted_at`; one active attempt per (student, quiz) enforced by partial unique index |
| `attempt_answers` | `selected_option` 0–3, `is_correct` computed at grading, `time_spent_ms` |
| `announcements` | `course_id NULL` = institution-wide; `publish_at`, `expires_at`; `author_id` |
| `audit_logs` | Append-only; `actor_id`, `action`, `entity_type`, `entity_id`, `before`/`after` JSONB, `ip`, `request_id` |
| `ingestion_jobs` | Durable job state: `stage`, `progress_pct`, `attempts`, `error`, timestamps. Redis holds only the queue message |

**Critical indexes**

```sql
CREATE INDEX idx_enrollments_user ON enrollments(user_id, course_id);
CREATE INDEX idx_chunks_topic ON chunks(topic_id);
CREATE INDEX idx_chunks_course_released ON chunks(course_id, released);
CREATE INDEX idx_topics_doc_release ON topics(document_id, release_state);
CREATE INDEX idx_tasks_course_due ON tasks(course_id, due_at);
CREATE INDEX idx_progress_student_status ON task_progress(student_id, status);
CREATE INDEX idx_attempts_student_quiz ON quiz_attempts(student_id, quiz_id);
CREATE UNIQUE INDEX uq_active_attempt ON quiz_attempts(student_id, quiz_id)
  WHERE submitted_at IS NULL;
```

### 8.3 Vector store design

**Collection:** `lms_chunks` (single collection; per-course collections rejected — they complicate cross-course queries and cap the number of collections).

| Element | Value |
|---|---|
| Vector ID | `{document_id}:{chunk_index}` (deterministic → idempotent upsert) |
| Embedding | 384-d float, L2-normalized, `hnsw:space = cosine` |
| Document text | Full chunk text (enables citation rendering without a DB round-trip) |
| Metadata | `course_id` (str), `document_id` (str), `topic_id` (str), `page_start` (int), `page_end` (int), `section_title` (str), `released` (bool), `pipeline_version` (int), `chunk_index` (int) |

**Chroma metadata types are scalar-only** — no arrays, no nested objects. This constrains filters to equality/`$in`/range, which is sufficient for the access pattern.

**Query filter (student path)** — built by `rag/access.py::student_vector_where`:

```python
where = {
    "$and": [
        {"course_id": {"$in": enrolled_course_ids}},
        {"released": {"$eq": True}},
    ]
}
if topic_id:
    where["$and"].append({"topic_id": {"$eq": topic_id}})
```

**Teacher path** omits the `released` clause but still scopes to courses the teacher owns.

**Release propagation (implemented):** the Postgres transaction commits first (topic state, `chunks.released`, task, audit), then `collection.update(ids=[...], metadatas=[{released: True, topic_id: ...}])` runs for that topic's vector IDs. A Chroma failure never rolls back the release; the response reports `chroma_synced=false` and `enqueue_reconcile()` schedules an hourly repair. Because Chroma is only ever behind Postgres, the lag can hide content but never expose it early.

**Unrelease (implemented, fail-closed):** Chroma metadata is flipped to `released=false` *before* the Postgres commit. If Chroma is unavailable the transaction rolls back and the API returns `503`, so the topic stays released and consistent rather than half-hidden.

**Reconciliation:** `reconcile_vectors()` walks active topics, compares each chunk's Postgres `released` flag with Chroma metadata (Postgres → Chroma only), repairs mismatches, and returns `(checked_topics, repaired_vectors)`. It runs hourly via the ARQ cron and after a failed release sync.

**Rebuild:** `chunks` retains text, so a full re-index is a replay of `embedder` + `vector_store.upsert` — no PDF re-parse required.

### 8.4 Release gate: four independent layers

| Layer | Mechanism | Failure mode prevented |
|---|---|---|
| **L1 — DTO** | `StudentTopicView` / `StudentChunkView` have no `release_state`, `released_by`, `released_at`, vector IDs, or chunk metadata | Accidental serialization of gate internals or audit fields |
| **L2 — SQL** | `topics_repo.list_released_for_scope` / `list_released_chunks_for_scope` join `documents`, require `release_state='released'`, `chunks.released=true`, and exclude soft-deleted rows | Hand-written queries drifting or leaking drafts |
| **L3 — Service** | `topic_service.assert_topic_released()` guards student reads; `_get_topic` re-checks `CourseScope` on every teacher mutation | Business-logic mistakes and cross-course access |
| **L4 — Vector** | `student_vector_where()` always includes `released=true` plus the enrolled course IDs | Retrieval returning unreleased chunks even if L1–L3 are bypassed |

**Regression tests (blocking CI, `tests/test_release_gate.py`):**

1. Seed one released and one draft topic, then call the student endpoints and the SQL repositories; assert the draft never appears and its chunk IDs are absent.
2. Query the fake vector store with `student_vector_where`; assert only released vector IDs come back.
3. Release a draft and unrelease it again; assert visibility and Chroma metadata flip both ways.
4. Force a Chroma write failure on release and assert the release still commits and queues reconciliation.
5. Force a Chroma write failure on unrelease and assert `503` with the topic still released.

### 8.5 File storage

- PDFs stored at `s3://{bucket}/documents/{course_id}/{document_id}.pdf` (dev: local volume mounted at `./data/uploads`).
- Access only via signed URLs (5-minute TTL) issued by the API after an authorization check.
- Checksum (SHA-256) computed on upload; duplicate within a course returns `409` with the existing document ID.
- Deletion is soft in Postgres, hard in storage after a retention window; Chroma vectors deleted immediately.

---

## 9. RAG Pipeline Architecture

### 9.1 Ingestion flow

```mermaid
sequenceDiagram
    autonumber
    participant T as Teacher (web)
    participant A as API
    participant S as Object storage
    participant D as Postgres
    participant Q as Redis/ARQ
    participant W as Worker
    participant C as Chroma
    participant G as Groq

    T->>A: POST /documents (multipart PDF)
    A->>A: validate MIME, magic bytes, size, encryption
    A->>S: store original
    A->>D: insert documents(status=queued), ingestion_jobs
    A->>Q: enqueue ingest_document(document_id)
    A-->>T: 202 {document_id, status: queued}

    Q->>W: deliver job
    W->>D: status=extracting
    W->>S: read PDF
    W->>W: PyMuPDF extract pages + headings
    W->>D: persist page_count, status=chunking
    W->>W: structure-aware chunking (600 tok / 90 overlap)
    W->>D: status=embedding
    W->>W: MiniLM batch embed (normalize, batch 64)
    W->>C: upsert chunks (released=false)
    W->>D: persist chunks metadata, status=topic_extraction
    W->>G: topic extraction (JSON schema)
    alt Groq unavailable / invalid JSON after retries
        W->>W: heading-based fallback topics
    end
    W->>D: insert topics (release_state=draft), status=ready
    W->>D: audit + notification
    T->>A: GET /documents/{id}/status
    A-->>T: {stage, progress_pct, topic_count}
```

**Stage contracts**

| Stage | Input | Output | Retry policy |
|---|---|---|---|
| validate | bytes | normalized PDF or typed error | no retry (user error) |
| extract | PDF | `[{page_no, text, headings[]}]` | 2 retries, then `failed` |
| chunk | pages | `[{index, text, page_start, page_end, section_title, tokens}]` | 2 retries |
| embed | chunks | 384-d vectors | 3 retries, exponential backoff |
| upsert | vectors + metadata | Chroma IDs | 3 retries (idempotent) |
| topics | chunk summaries | topic records | 2 retries → heading fallback |
| finalize | — | `ready` + notification | idempotent |

**Chunking rules**

1. Split on detected headings (font-size/outline heuristics) into sections.
2. Within a section, pack sentences into ~600-token windows with ~90-token overlap, never splitting mid-sentence.
3. Drop chunks under 40 tokens unless they are the only content of a section.
4. Prepend `section_title` to the embedded text (not the stored `text`) to improve topical recall.
5. Preserve page spans so citations point at the correct page even across chunk boundaries.

**Embedding details:** model loaded once per worker process (`lru_cache`); `normalize_embeddings=True`; batch size 64; first run downloads ~90 MB into a baked-in cache directory (`HF_HOME`), so production does not fetch at runtime. MiniLM's context is 256 tokens while chunks target ~600, so long chunks are split into overlapping token windows and their normalized vectors are mean-pooled before upsert; short chunks use a single window.

### 9.2 Retrieval

```
query → normalize → embed (MiniLM) → Chroma query (top 12, filtered)
      → drop results below similarity threshold (default 0.30, calibrated)
      → MMR diversify (λ = 0.7) → top 6
      → assemble numbered context blocks with [document, page] labels
```

- **Threshold calibration:** measured against a labelled eval set (§9.7). If the best similarity is below threshold, the assistant returns the refusal response rather than guessing.
- **MMR** prevents six near-duplicate chunks from the same page crowding out a second relevant section.
- **Query expansion (P2):** optional Groq rewrite of conversational follow-ups into standalone queries; disabled in v1 to keep latency and cost predictable.

**Implemented (Phase 6).** `app/rag/retriever.py` embeds the normalized question with MiniLM and queries Chroma with `student_vector_where(...)`, which always combines `course_id IN scope` with `released=true` and adds `topic_id` when supplied. Similarity is `1 - cosine_distance`, clamped to `[0, 1]`; hits below `CHROMA_MIN_SIMILARITY` (0.30) are discarded before ranking, and MMR selects at most `CHROMA_FINAL_K` (6).

The Chroma filter is not the final authority. Every above-threshold vector ID is resolved back to Postgres by `rag_repo.resolve_visible_chunks`, which re-checks `chunks.released`, `topics.release_state='released'`, non-deleted topic and document, `documents.status='ready'`, and the same course scope. A candidate that Chroma still reports as released but Postgres does not is dropped and logged as `assistant_candidate_hidden`, so stale vector metadata can only hide content, never expose it. An empty result after either gate produces the refusal with zero citations.

### 9.3 Generation

- Groq chat completion, `temperature=0.2`, `max_tokens=1024`, `reasoning_effort=medium` (configurable via `GROQ_REASONING_EFFORT`), one retry on timeout.
- Context blocks are delimited (`<ctx id="1" doc="..." page="12">…</ctx>`) and numbered; the system prompt instructs the model to cite `[1]`-style references.
- Post-processing validates that every citation index exists in the retrieved set; invalid citations are stripped, and an answer with zero valid citations is downgraded to the refusal response.
- The API streams tokens to the client (SSE) but only after retrieval and guard checks pass; the final citation list is sent as a terminal event.
- **Prompt-injection defense:** PDF text is data, never instructions. The system prompt states that content inside `<ctx>` is untrusted source material. Chunks containing imperative meta-instructions ("ignore previous instructions") are flagged and logged; the answer is still generated but flagged for review.
- **PII:** prompts contain chunk text, page numbers, and the user's question only. No names, emails, or IDs are included.

**Implemented (Phase 6).** `app/rag/generator.py` deliberately generates the full answer *before* the SSE response starts, because citation validation needs the complete text. Groq is called with `GROQ_ANSWER_RETRIES` attempts; an empty response or exhausted retries raises `AnswerGenerationUnavailable`, which the service logs as `generation_failed` and the router still terminates with `citations` and `done`.

Citation markers are regex-validated against the retrieved chunk count. Out-of-range numbers are removed, duplicate numbers are collapsed, and the answer is normalized before it is emitted. Unicode bracket variants (`【1】`, `［1］`, and similar) are folded to ASCII `[1]` first; without that, a correct grounded answer from a model that mirrors CJK typography was being downgraded to the refusal. Zero valid citations always produce exactly `This isn't covered in your released material.` with an empty citation list.

After validation the router emits the already-validated text as `token` chunks, then a terminal `citations` event and a `done` event. The query log is written before the response is returned, so a client disconnect cannot lose the audit row. `rag_query_logs` records `question_hash`, chunk IDs, `max_similarity`, `refusal`, `injection_flag`, latency, token counts, and `prompt_version`; `question_text` is written only when `RAG_STORE_QUESTIONS=true`.

The embedding model is loaded in a background task during API startup (skipped when `APP_ENV=test`). The model takes tens of seconds to load on a cold process, so warming it keeps `/readyz` fast while removing that cost from the first student question. The image bakes the MiniLM weights and then sets `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`; without those flags, a runtime with restricted egress spent minutes in Hugging Face retry backoff even though the weights were local. With offline mode and warmup, a live Compose run completed startup warmup in under 40 seconds and answered covered questions in 1.6–2.8 seconds server-side (1.8–3.0 seconds through the web proxy). `reasoning_effort=medium` is the default: a live comparison on a covered question produced valid citations 2/2 at medium, while `low` produced false refusals 2/2 because the model omitted citations entirely.

### 9.4 Topic extraction (implemented)

1. Build a compact outline: for each chunk, `{id, section_title, first 200 chars}` (bounded by `GROQ_TOPIC_BATCH_TOKENS`; map-reduce across batches for very large documents).
2. Call Groq (`app/rag/topics.py::GroqTopicExtractor`) with a strict JSON schema (versioned prompt `rag/prompts/topics_v1.txt`), `temperature=0.1`, and `GROQ_TOPIC_RETRIES` retries.
3. Validate: unique titles, page ranges within the document, every `chunk_id` exists, no chunk assigned twice, coverage ≥ 90%. Unmapped chunks attach to the nearest topic and are flagged `needs_review`.
4. On repeated schema failure, provider outage, or a missing `GROQ_API_KEY`, fall back to heading-based grouping (deterministic, one topic per heading group) and mark every topic `needs_review`.
5. Insert topics as `draft` with `documents.topics_source` set to `llm` or `fallback`; **never auto-release**. Missing LLM credentials never fail ingestion.

### 9.5 MCQ generation (v1: MCQ only, implemented)

1. Input: the released topic's chunks only (bounded by `QUIZ_MAX_CHUNK_TOKENS`, default 8000), assembled as numbered `<chunk id="…">` blocks so every proposal can cite a real source.
2. Prompt (`rag/prompts/quiz_v1.txt`) requests `QUIZ_TARGET_QUESTIONS` (default 10) questions as schema-constrained JSON: `stem`, `options[4]`, `correct_index`, `explanation`, `difficulty`, `source_chunk_id`.
3. Deterministic validation before persisting:
   - exactly 4 distinct, non-empty options;
   - `correct_index ∈ [0,3]`;
   - `source_chunk_id` exists in the topic's chunks;
   - stem length is bounded and does not duplicate another stem (normalized hash);
   - no option is a trivial negation of another.
4. Invalid items are discarded; the job regenerates for up to `QUIZ_GENERATION_ROUNDS` (default 3) to reach the target, then persists what passed (minimum `QUIZ_MIN_QUESTIONS`, default 5) and leaves the quiz `needs_review`. If nothing valid survives, the quiz is marked `generation_status='failed'` with `generation_error` instead of silently disappearing.
5. Questions persist with `approved = false`; teacher approval publishes the quiz. `auto_approve` is a per-quiz flag, off by default, and publishes only when enough questions passed.
6. The ARQ `generate_quiz` job owns the state machine `queued → generating → ready|failed`; generation replaces draft questions idempotently inside the persist transaction.
7. **Grading:** server-side comparison of `selected_option` against `correct_option`; correct answers and explanations are never serialized to the client before submission. `attempt_answers` stores the choice and the computed result for analytics.

The LLM only *proposes*. `app/rag/quizzes.py` owns validation and `quiz_service` owns persistence, publishing rules, and the observable generation state.

### 9.6 Token and cost budgets

| Path | Context budget | Output cap | Notes |
|---|---|---|---|
| Student answer | 6 chunks × ~600 tok + 400 system ≈ 4k | 1024 | Fits every Groq model in use |
| Topic extraction | ~12k (map-reduce beyond) | 2048 | One call per document (or per map shard) |
| Quiz generation | ~8k (`QUIZ_MAX_CHUNK_TOKENS`) | 4096 (`GROQ_QUIZ_MAX_TOKENS`) | One call per round, ≤ 3 rounds |

Rate limits: 20 assistant queries/hour/student (Redis sliding window when `REDIS_URL` is set, with an in-process fallback for single-worker development), 50 topic/quiz generations/hour/teacher, global daily token ceiling with an alert at 80%.

### 9.7 RAG evaluation

A versioned eval set (`eval/rag_cases.yaml`) is run in CI nightly and before prompt/model changes:

| Metric | Definition | Gate |
|---|---|---|
| Retrieval hit@6 | Correct source chunk present in top 6 | ≥ 0.90 |
| Citation validity | Answers whose citations all exist in retrieved set | ≥ 0.98 |
| Faithfulness | Answer claims supported by context (LLM-as-judge + spot review) | ≥ 0.90 (manual spot review in v1; automated judge planned) |
| Refusal accuracy | Unreleased/out-of-scope questions refused | ≥ 0.95 |
| Leak rate | Unreleased chunk retrieved in student path | **0** |

**Implemented (Phase 6).** `backend/eval/run_eval.py` is an offline, deterministic harness. It loads `rag_cases.yaml` (41 cases covering answerable, cross-chapter, unreleased, out-of-scope, injection, and stale-release-metadata scenarios), builds an in-memory SQLite database and a local cosine vector store, and uses a deterministic hashing bag-of-words embedder instead of MiniLM/Chroma/Groq. That keeps the gate fast and runnable in CI without external services; the real stack is exercised by the manual walkthrough below.

One corpus case deliberately marks a draft topic's Chroma metadata as `released=true`. The harness expects the Postgres re-check to hide it, so the leak gate proves the database is the final authority rather than the vector filter. `run_eval.py` exits non-zero when any gate is missed.

Current results: 41 cases; hit@6 **1.000**, citation validity **1.000**, refusal accuracy **1.000**, leak rate **0.000**; all gates PASS. Injection cases are flagged 1/1 (informational). Faithfulness remains a manual spot review in v1.

---

## 10. Key Request Flows

### 10.1 Topic release

```mermaid
sequenceDiagram
    autonumber
    participant T as Teacher
    participant A as API
    participant D as Postgres
    participant C as Chroma
    participant W as Worker

    T->>A: POST /topics/{id}/release {week_no, due_at, generate_quiz=true}
    A->>A: authorize (owns course) + document is ready + topic is draft
    A->>D: BEGIN
    A->>D: update topics set release_state='released', released_by/at
    A->>D: upsert tasks (published; republish when archived)
    A->>D: insert notifications for enrolled students
    A->>D: update chunks set released=true where topic_id
    A->>D: if generate_quiz: insert draft quiz (one per topic)
    A->>D: insert audit_log
    A->>D: COMMIT
    A->>C: update metadatas {released=true, topic_id} per chunk
    A->>W: enqueue generate_quiz(quiz_id) after commit (never blocks release)
    A-->>T: 200 {topic_id, task_id, task_status, released_at, chroma_synced, reconcile_queued, quiz_id, quiz_queued}
```

**Consistency rule:** Postgres commits first. Chroma is updated after commit; if the write fails the release still stands, `chroma_synced=false`, and `enqueue_reconcile()` schedules the hourly repair. A brief window where Chroma lags only ever *hides* content (fails closed), never exposes it.

**Unrelease reverses the order:** Chroma metadata is hidden first, then Postgres commits. If Chroma fails the request returns `503` and the transaction rolls back, so a released topic is never left with half-hidden vectors.

**Release semantics (implemented through Phase 5):** the response creates/republishes the task, fans out one `task_released` notification per actively enrolled student, and—when `generate_quiz` is true (the default)—creates one draft quiz for the topic inside the same transaction. `quiz_queued` reports whether the post-commit `generate_quiz` enqueue succeeded; an enqueue failure is logged and never fails the release, leaving a draft a teacher can regenerate. Releasing an already-released topic is idempotent and returns the existing task and quiz without fan-out or a second generation. Unreleasing archives the task and hides the quiz (and its open attempts) while retaining submitted attempts and results. Scheduled release is not implemented, and no release ever happens automatically.

### 10.2 Student assistant query

```mermaid
sequenceDiagram
    autonumber
    participant S as Student
    participant A as API
    participant R as Rate limiter
    participant D as Postgres
    participant C as Chroma
    participant G as Groq

    S->>A: POST /student/ask {course_id?, topic_id?, question}
    A->>A: authz → enrolled course IDs (must be non-empty)
    A->>A: validate question + optional topic visibility
    A->>R: check 20/hour budget
    A->>A: embed question (MiniLM)
    A->>C: query(top_k=12, course_id IN scope AND released=true)
    A->>D: re-check chunks, topics, documents, and course scope
    alt no visible result above threshold
        A->>D: log refusal (zero chunk IDs, zero citations)
        A-->>S: 200 SSE refusal token + citations[] + done
    else grounded
        A->>G: chat completion (non-streaming)
        G-->>A: full answer
        A->>A: validate [n] citations; zero valid → refusal
        A->>D: log chunk IDs, similarity, latency, tokens, refusal flag
        A-->>S: SSE validated tokens + terminal citations + done
    end
```

1. **Pre-stream guards.** Auth, course scope, topic visibility, question validation, rate limiting, retrieval, generation, citation validation, and query logging all complete before the first SSE byte. A guard failure returns a normal RFC 7807 response (`404` for out-of-scope, `429` for the hourly limit, `422` for an invalid question) rather than a partially started stream.
2. **Topic visibility.** An in-scope topic that is draft, deleted, or attached to a non-ready document is treated as having no released material and returns the refusal. A topic outside the caller's scope, or one that does not belong to the requested `course_id`, is `404` so its existence is not leaked.
3. **Two-layer retrieval gate.** Chroma filters on course scope and `released=true`; Postgres then re-checks every candidate. The database is the source of truth for release state, so stale vector metadata cannot expose a draft chunk.
4. **Generation is not streamed from the provider.** The service waits for the complete answer, validates its citations, and only then returns a `StreamingResponse` that replays the validated text as `token` events. A generation failure emits an `error` event but still sends the terminal `citations` and `done` events, so the client always has a defined end state.
5. **Logging and privacy.** The query log is committed before the stream starts. It stores a SHA-256 question hash and never the raw question unless `RAG_STORE_QUESTIONS=true`. Logged chunk IDs are only those that passed both release gates.

Phase 6 verification: the SQLite suite adds `test_assistant.py`, `test_retrieval.py`, `test_refusal.py`, and `test_generator_request.py` (24 tests; 158 total). The offline eval suite reports hit@6 **1.000**, citation validity **1.000**, refusal accuracy **1.000**, and leak rate **0.000** across 41 cases. A final live Compose walkthrough on the `medium` image, through `http://localhost:3000`, produced a cited chloroplast answer (`refusal=false`, two citations, 1.8 s wall / 1.6 s server), refused an in-scope draft topic and an uncovered question with zero citations, and returned `404` for a course outside the student's enrollment. `rag_query_logs` rows had `question_text IS NULL`, and warmup logged `assistant_embedder_warmed` in under 40 seconds. An earlier walkthrough exposed one production-only issue: Groq emitted fullwidth citation brackets (`【1】`), which the validator now normalizes to `[1]` before rejecting an otherwise grounded answer.

### 10.3 Quiz attempt (implemented)

```mermaid
sequenceDiagram
    autonumber
    participant S as Student
    participant A as API
    participant D as Postgres

    S->>A: POST /student/quizzes/{id}/attempts
    A->>D: visibility check (published quiz, released topic, enrolled) + limit
    alt open attempt exists
        A-->>S: 200 attempt state (resume, no answer key)
    else no open attempt
        A->>D: insert quiz_attempt (partial unique index uq_active_attempt)
        A-->>S: 200 attempt state (questions without correct_option/explanation/source_chunk_id)
    end
    S->>A: PATCH /student/attempts/{id} {answers[]}
    A->>D: upsert selected_option for the caller's open attempt
    S->>A: POST /student/attempts/{id}/submit {answers[]}
    A->>D: BEGIN — merge with autosaved answers, grade vs correct_option
    A->>D: write a row for every question (skipped = NULL, is_correct=false)
    A->>D: store score/max_score/percentage/passed/submitted_at — COMMIT
    A-->>S: 200 result {score, review with correct_option, explanation, source_chunk_id}
```

1. **Visibility and limits.** The student sees a quiz only when it is `published`, its topic is released and not deleted, its document is not deleted, the course is active, and the caller has an active enrollment. A foreign or hidden quiz is `404`, never `403`. `attempt_limit = 0` means unlimited.
2. **Start is idempotent.** `POST /api/v1/student/quizzes/{id}/attempts` returns the existing open attempt if one exists, so a page refresh resumes instead of consuming another attempt. The DB enforces one open attempt per `(student_id, quiz_id)` with `uq_active_attempt`.
3. **Autosave.** `PATCH /api/v1/student/attempts/{id}` upserts the caller's selections and returns `{saved, answered, total}`. The web runner sends the local answer set 700 ms after the last selection.
4. **Submit.** `POST /api/v1/student/attempts/{id}/submit` merges the request with autosaved answers, grades deterministically in one transaction, and returns `score`, `max_score`, `percentage`, `passed`, and a per-question review. Unanswered questions are still recorded (`selected_option=null`, `is_correct=false`) so analytics can distinguish skipped from missing data.
5. **Immutability.** Saves after submission return `409`; a repeated submit returns the stored result rather than recomputing it. Results remain readable through `GET /api/v1/student/attempts/{id}/result` even after the topic is unreleased. Retakes create new rows only when the attempt limit allows.
6. **Answer-key boundary.** Start, resume, list, and autosave payloads never contain `correct_option`, `explanation`, or `source_chunk_id`; those fields appear only in the submitted result review. Attempts belonging to another student are `404`.

Phase 5 verification: the SQLite suite adds `test_quiz_generation.py`, `test_quiz_attempts.py`, and `test_quiz_security.py` (24 tests; 134 total). `scripts/smoke_phase5.py` ran the real Compose stack with a live Groq key and passed **53/53** checks: 10 valid grounded questions from a real released topic, draft invisibility, edit-reset approval, publish, no student answer-key leakage, idempotent start, autosave+submit merging, a hand-computed 5/10 score, skipped-answer storage, post-submit `409`, repeat-submit idempotence, `attempt_limit=1`, regenerate rejection, and unrelease/re-release reusing the same quiz.

### 10.4 Analytics query path

- Pre-aggregated on read using indexed SQL; no materialized views in v1 (dataset is small).
- Cohort KPIs: one query joining `tasks`, `task_progress`, `enrollments` grouped by course. The
  completion denominator is `active students × published tasks from released, non-deleted topics`.
- Student rows: completion ratio + `max(last_active_at)` + `count(distinct date(activity))` over
  `task_progress.updated_at` unioned with `quiz_attempts.submitted_at`.
- Topic accuracy: `attempt_answers` joined to `questions` grouped by `topic_id`; unknown answers
  are recorded as incorrect, so skipped questions still count.
- Knowledge gaps rank topics and questions by ascending accuracy; a student is flagged when their
  average is below 60% on at least two topics.
- The optional `week` filter applies `tasks.week_no` to completion and to attempts tied to that
  week's published tasks. Topic analytics intentionally aggregate the whole course.
- Cached in Redis for 60 s per `(course_id, filter_hash)`; invalidated on release, completion, or
  submission events. Without Redis the same TTL is enforced in-process.
- `last_active_at` is written at most every 5 minutes per user to keep analytics cheap, and is
  indexed by `ix_users_last_active_at`.

Phase 7 verification: the SQLite suite builds 10 students × 3 topics × 2 attempts with
hand-computable numbers and asserts cohort KPIs, topic heatmap cells, gaps, sorting, pagination,
drill-down contents, ownership `404`, student `403`, and cache invalidation.

### 10.5 Admin oversight (implemented)

- **Capability matrix.** `permissions(key, role, enabled)` is seeded by migration `0010` and read
  through `PermissionService.can` with a 30-second cache. Admins bypass every check. Missing rows
  resolve to conservative defaults: self-enroll and teacher course creation off; assistant and
  quiz auto-approval on.
- **Enforcement points.** `student.assistant_enabled` gates the assistant route before any
  retrieval; `quiz.auto_approve` gates publishing after generation; `teacher.create_course` gates
  course creation in the service and auto-enrolls the creator; `student.self_enroll` gates the
  idempotent self-enrollment route.
- **Audit viewer.** `GET /admin/audit-logs` filters by actor, action, entity, and time window and
  pages newest-first by `(created_at, id)` cursor. The actor name is joined in SQL. Audit rows are
  append-only and survive hard deletes of the entities they describe.
- **Content repository.** `GET /admin/content` aggregates course, uploader, topic, released-topic,
  and chunk counts with grouped subqueries. Delete requires zero released topics, then removes
  attempts, quizzes, questions, tasks, progress, topics, chunks, and ingestion jobs in Postgres,
  the Chroma vectors by `document_id`, and the stored PDF. The audit row keeps the pre-delete
  snapshot.
- **CSV import.** Rows are validated individually; malformed names, emails, roles, and unknown
  course codes are errors, while duplicate/existing emails are skips. `partial` inserts valid
  rows; `atomic` inserts nothing if any row fails. The attempt is audited with counts and created
  ids.
- **System health.** Postgres, Redis, Chroma, and Groq are probed independently. Groq failure is
  `degraded` with HTTP 200; database, Redis, or Chroma failure makes the overall status degraded
  too, and the latest failed ingestion is included for operators. No impersonation exists in v1.

Phase 8 verification: 25 new SQLite tests (191 total) cover admin-only enforcement, capability
defaults and behavior changes, admin bypass, audit coverage and append-only behavior, cursor
filtering, CSV partial/atomic semantics, temporary-password login, content aggregation, the
released-topic delete guard, and row/vector/storage removal.


---

## 11. API Architecture

### 11.1 Conventions

| Concern | Decision |
|---|---|
| Base path | `/api/v1` |
| Auth | `access_token` + `refresh_token` in `httpOnly`, `Secure`, `SameSite=Lax` cookies; `Authorization: Bearer` accepted for tooling |
| CSRF | Double-submit token required on all non-GET requests |
| Errors | RFC 7807 `application/problem+json` with `type`, `title`, `status`, `detail`, `request_id` |
| Pagination | Cursor-based (`?cursor=&limit=`), max 100 |
| Idempotency | `Idempotency-Key` honored on upload, release, and submit |
| Versioning | URL version; additive changes preferred |
| Validation | Pydantic schemas at the edge; unknown fields rejected |
| Long operations | `202 Accepted` + resource status endpoint (never a blocking upload) |

### 11.2 Authorization model

```
authenticate(request) → Principal{user_id, role, course_roles}
authorize(principal, action, resource_scope)
```

- `require_role("admin")` for admin routers.
- `require_course_teacher(course_id)` for teacher mutations — ownership, not just role.
- Student routers derive `CourseScope` from `enrollments` on every request; the client-supplied `course_id` is validated against that scope and never trusted.
- Admin impersonation ("view as") issues a short-lived, read-only, audit-logged token.

---

## 12. Async Processing

| Job | Trigger | Idempotency key | Timeout | Retries |
|---|---|---|---|---|
| `ingest_document` | upload | `document_id + checksum` | 15 min | 3, backoff 30s/2m/5m |
| `generate_quiz` | release (if requested) | `quiz_id` | 5 min | 2 |
| `send_notifications` | release / announcement | `event_id` | 60 s | 3 |
| `reconcile_vectors` | cron (hourly, implemented) | run date | 10 min | 1 |
| `apply_scheduled_releases` | cron (1 min) — **not implemented**; scheduled release remains out of v1 scope | — | 2 min | 1 |
| `cleanup_expired` | cron (daily) | — | 10 min | 1 |

- Job state lives in `ingestion_jobs` / job tables; Redis is disposable. If Redis is lost, a startup sweep re-enqueues jobs stuck in a non-terminal stage for > 10 minutes.
- Workers are horizontally scalable; ingestion concurrency is bounded by a semaphore (default 2 per worker) to protect CPU during embedding.
- The embedding model is loaded lazily per worker process; workers are long-lived to amortize the ~2 s load.
- Failed jobs expose a stable `error_code` (`ENCRYPTED_PDF`, `NO_TEXT_LAYER`, `LLM_UNAVAILABLE`, `VECTOR_WRITE_FAILED`) mapped to human-readable UI copy.

---

## 13. Security Architecture

### 13.1 Threat model (abridged STRIDE)

| Threat | Vector | Control |
|---|---|---|
| Elevation of privilege | Student calls teacher/admin endpoint | Server-side role + ownership checks; per-route tests |
| Information disclosure | Unreleased content via API or RAG | Triple-layer release gate (§8.4) |
| IDOR | Guessing `document_id` / `course_id` | Every resource load goes through a scoped repository |
| Malicious file | Crafted PDF (zip bomb, JS, path traversal) | Magic-byte check, size/page caps, PyMuPDF sandboxed parsing, no filename-derived paths |
| Prompt injection | Instructions embedded in PDF text | Context delimiting, system-prompt hardening, citation validation, flagging |
| XSS | Rich-text announcements | Sanitize on write (allowlist) + render as sanitized HTML |
| Session theft | XSS/CSRF | `httpOnly` cookies, CSRF double-submit, short access TTL, refresh rotation + revocation table |
| Brute force | Login endpoint | Per-IP + per-account rate limits, exponential lockout, Argon2id |
| SSRF | Announcement link previews (future) | Disabled in v1; allowlist if added |
| Data exfiltration via LLM | PII in prompts | Strip identifiers; log chunk IDs only; egress allowlist to Groq |
| Supply chain | Dependencies | Lockfiles, `pip-audit`/`npm audit` in CI, pinned model revision |

### 13.2 Secrets and configuration

- `.env` only; `.env.example` committed with placeholders. Startup fails fast if `GROQ_API_KEY`, `DATABASE_URL`, or `SESSION_SECRET` is missing.
- No secret ever reaches the browser bundle. Frontend receives only `NEXT_PUBLIC_API_BASE_URL`.
- Container images run as non-root; read-only root filesystem except `/tmp` and the model cache.

### 13.3 Audit events

`user.create/update/deactivate/delete`, `user.import`, `role.change`, `document.upload/delete/reingest/download`, `topic.release/unrelease/edit`, `quiz.approve`, `announcement.publish`, `permission.update`, `enrollment.self_create`, `impersonation.start/stop` (not implemented in v1), `login.failure`. Each record includes actor, IP, request ID, and before/after for mutations. Rows are append-only: there is no delete endpoint and deleting a user or document leaves the audit snapshot intact.

---

## 14. Performance & Scalability

### 14.1 Capacity model (v1 targets)

| Resource | Estimate |
|---|---|
| Users | 5,000 |
| Courses | 200 |
| Documents | 2,000 (avg 150 pages) |
| Chunks | ~1,000,000 |
| Chroma memory | ~2–3 GB (384-d + HNSW graph) |
| Postgres | < 20 GB |
| Peak API | 50 req/s |
| Peak assistant queries | 2 req/s |

A single 4-vCPU / 8 GB node runs API + worker + Chroma comfortably at this scale. Postgres is managed.

### 14.2 Scaling levers (in order)

1. Add API replicas behind a load balancer (stateless, cookie/JWT auth).
2. Add worker replicas; embedding is CPU-bound, so scale with cores.
3. Move Postgres to a larger instance, then add a read replica for analytics.
4. Cache analytics and course metadata in Redis (already in place for KPIs).
5. If Chroma exceeds ~5M chunks: shard by `course_id` across Chroma instances behind the `VectorStore` adapter, or migrate to a managed vector DB — the adapter boundary makes this a configuration change, not a rewrite.

### 14.3 Hot-path optimizations

- Composite indexes for every student list query (§8.2).
- `task_progress` upsert instead of read-modify-write.
- Denormalized `released` on `chunks` avoids joins during retrieval.
- Retrieval uses `include=["metadatas","documents","distances"]` and no embeddings (saves bandwidth).
- Analytics cached 60 s; `last_active_at` throttled.
- PDF text extraction is the slowest stage (~10–20 s for 200 pages); everything after it is parallelizable per batch.

---

## 15. Reliability & Failure Modes

| Failure | User-visible behavior | System behavior |
|---|---|---|
| Groq timeout/outage (ingestion) | Ingestion completes with heading-based topics, flagged "AI unavailable" | Job succeeds, teacher notified, can re-run extraction later |
| Groq outage (student ask) | "Assistant temporarily unavailable, try again" | No partial/fabricated answer; circuit breaker opens for 60 s |
| Chroma unavailable | Teacher sees content; student assistant unavailable | Read paths for tasks/quizzes unaffected (Postgres) |
| Chroma write fails after release | Content visible in task list; assistant may lag | Reconciliation job repairs metadata within an hour; fails closed |
| Redis lost | No new jobs processed until restart | Job rows in Postgres re-enqueued by startup sweep |
| Worker crash mid-ingest | Status shows `failed` with retry | ARQ retry resumes from last completed stage (stage checkpoints) |
| Duplicate upload | `409` with existing document link | Checksum lookup |
| Postgres failover | Brief 5xx | Managed HA; app retries idempotent reads |
| Model download unavailable | Worker fails fast | Model baked into image/cache volume |

**Backups:** Postgres PITR daily + WAL; Chroma snapshot daily (derived, but avoids a 30-minute rebuild); object storage versioning on.

---

## 16. Observability

### 16.1 Logs

Structured JSON via `structlog`: `timestamp`, `level`, `request_id`, `user_id` (hashed where appropriate), `route`, `status`, `duration_ms`, `job_id`, `stage`. RAG logs add `chunk_ids`, `top_k`, `max_similarity`, `refusal`, `prompt_version`, `tokens_in/out`, `model`.

### 16.2 Metrics (Prometheus)

| Metric | Type | Alert |
|---|---|---|
| `http_request_duration_seconds` | histogram | P95 > 1 s for 10 min |
| `ingestion_duration_seconds{stage}` | histogram | P95 > 5 min |
| `ingestion_failures_total{error_code}` | counter | > 5% over 15 min |
| `rag_query_duration_seconds` | histogram | P95 > 8 s |
| `rag_refusal_rate` | gauge | > 40% (retrieval or prompt regression) |
| `rag_leak_attempts_total` | counter | **> 0 (page immediately)** |
| `groq_tokens_total` | counter | Daily budget 80% |
| `queue_depth{queue}` | gauge | > 50 for 10 min |
| `chroma_query_duration_seconds` | histogram | P95 > 300 ms |

### 16.3 Tracing

OpenTelemetry spans across `api → chroma`, `api → groq`, and `worker → extract/embed/upsert/topics`, correlated by `request_id` / `job_id`. Trace sampling 10% in production, 100% in staging.

### 16.4 Dashboards

1. **Platform health** — error rate, latency, queue depth, DB connections.
2. **Ingestion** — throughput, stage durations, failure codes, topic counts.
3. **RAG quality** — refusal rate, citation validity, hit rate (from eval + sampled production), token spend.
4. **Learning** — daily active users, tasks completed, quiz attempts, average score.

---

## 17. Deployment

### 17.1 Local development

```yaml
# docker-compose.yml (abridged)
services:
  postgres:   { image: postgres:16, ports: ["5432:5432"] }
  redis:      { image: redis:7-alpine, ports: ["6379:6379"] }
  chroma:     { image: chromadb/chroma:1.5.9, ports: ["8001:8000"],
                volumes: ["chroma-data:/data"] }
  api:        { build: ./api, command: uvicorn app.main:app --reload, ports: ["8001:8000"] }
  worker:     { build: ./api, command: arq workers.settings.WorkerSettings }
  web:        { build: ./web, command: npm run dev, ports: ["3000:3000"] }
volumes:
  chroma-data:
```

One command (`docker compose up`) plus `alembic upgrade head` and `python -m app.seed` yields the PRD's seed data.

### 17.2 Production topology

```mermaid
flowchart TB
    CDN["CDN / TLS termination"] --> WEB["web replicas (2+)"]
    WEB --> API["api replicas (2+)"]
    API --> PG[("Managed PostgreSQL<br/>HA + PITR")]
    API --> RD[("Managed Redis")]
    API --> CH[("Chroma node<br/>persistent SSD")]
    API --> S3[("Object storage")]
    API --> GQ["Groq API"]
    WK["worker replicas (1+)"] --> PG
    WK --> RD
    WK --> CH
    WK --> S3
    WK --> GQ
    OBS["Logs / metrics / traces"] -.-> WEB
    OBS -.-> API
    OBS -.-> WK
```

- Images built once, promoted by digest through `dev → staging → prod`.
- Chroma on a persistent SSD volume with a single writer; snapshotted daily.
- Health endpoints: `/healthz` (liveness), `/readyz` (DB + Redis + Chroma reachability). Groq is a *degraded* dependency, not a readiness gate.
- Migrations run as a pre-deploy job, never from app startup.

### 17.3 Environments

| Env | Data | LLM | Notes |
|---|---|---|---|
| local | seeded | Groq (real key) or stub | Stub returns canned JSON for offline dev |
| staging | anonymized copy | Groq | Full eval suite runs here |
| prod | live | Groq | Budget alerts, audit retention |

---

## 18. Testing Strategy

| Level | Scope | Tooling | Gate |
|---|---|---|---|
| Unit | chunker, validators, graders, prompt assembly | pytest | 90% on `rag/` and `services/` |
| Integration | API + Postgres + Chroma + Redis | testcontainers | All routes, all roles |
| Contract | OpenAPI schema ↔ frontend client | generated types + schema diff | No breaking change without version bump |
| E2E | Upload → topics → release → student task → quiz | Playwright | Happy paths for all three portals |
| Security | RBAC matrix, IDOR, release gate | pytest parametrized over routes × roles | 100% of mutating routes covered |
| RAG eval | Retrieval, faithfulness, refusal, leak | `eval/rag_cases.yaml` | §9.7 thresholds |
| Load | 50 req/s CRUD, 2 req/s RAG | k6 | P95 targets met |

**CI pipeline:** lint → typecheck → unit → build images → integration (testcontainers) → security/RBAC suite → e2e (staging) → RAG eval (nightly + on prompt changes) → deploy.

---

## 19. Architecture Decision Records (summary)

| ADR | Decision | Alternatives considered | Consequence |
|---|---|---|---|
| ADR-001 | FastAPI owns all business logic; Next.js is UI + BFF | Next.js route handlers for everything; two backends | One authorization surface; Python RAG ecosystem available |
| ADR-002 | Chroma in server mode over HTTP | Embedded persistent client | Safe multi-process access; one extra container |
| ADR-003 | `released` denormalized into chunk metadata | Join release state at query time; separate released collection | O(1) filtering; requires reconciliation job |
| ADR-004 | ARQ + Redis, durable state in Postgres | Celery; in-process background tasks | Lightweight, async-native; jobs survive Redis loss |
| ADR-005 | JWT access/refresh in `httpOnly` cookies | Server-side sessions | Stateless verification at the edge; refresh revocation table adds DB reads |
| ADR-006 | Local MiniLM embeddings, Groq for generation | Hosted embeddings (OpenAI) | No embedding cost/API key; CPU-bound ingestion |
| ADR-007 | MCQ-only quizzes, deterministic grading | LLM-graded short answers | Cheap, instant, auditable; limits assessment depth (revisit in v2) |
| ADR-008 | Course = offering; many-to-many enrollments | Separate cohort/section table | Simpler v1 schema; multi-section needs a migration (§8.2) |
| ADR-009 | Postgres commits before Chroma updates | Two-phase commit across stores | Brief fail-closed lag instead of distributed transactions |
| ADR-010 | Single `lms_chunks` collection with metadata filters | Collection per course | Simple cross-course queries; relies on metadata index quality |

---

## 20. Future Evolution

1. **Multi-section cohorts** — add `cohorts` under `courses`; enrollments move to `cohort_id`; analytics gain a section filter.
2. **Richer assessments** — short answer with rubric + LLM-assisted grading behind a new `grading_strategy` interface; the attempt model already stores per-question answers.
3. **Non-PDF ingestion** — `extract.py` becomes a registry of extractors (DOCX, PPTX, HTML, video transcripts); the rest of the pipeline is unchanged.
4. **Hybrid search** — add BM25/keyword scores and fuse with vector scores (RRF) for terminology-heavy material.
5. **Query rewriting & conversation memory** — bounded session history for follow-up questions, with the same release-gate filter.
6. **Adaptive practice** — use `attempt_answers` to recommend topics and generate targeted question sets.
7. **LTI/SCORM** — expose the course as an LTI tool for external LMS interoperability.
8. **Managed vector DB** — swap the `VectorStore` adapter when chunk count exceeds single-node comfort.

---

## Appendix A — Configuration surface

```dotenv
# App
APP_ENV=development
API_BASE_URL=http://localhost:8000
WEB_BASE_URL=http://localhost:3000
DATABASE_URL=postgresql+asyncpg://lms:lms@postgres:5432/lms
REDIS_URL=redis://redis:6379/0
SESSION_SECRET=change-me
ACCESS_TOKEN_TTL_MIN=15
REFRESH_TOKEN_TTL_DAYS=7

# Vector store
CHROMA_HOST=chroma
CHROMA_PORT=8000
CHROMA_COLLECTION=lms_chunks
CHROMA_TOP_K=12
CHROMA_FINAL_K=6
CHROMA_MIN_SIMILARITY=0.30

# Embeddings
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_DIM=384
EMBEDDING_BATCH_SIZE=64
HF_HOME=/models
HF_HUB_OFFLINE=1                 # weights are baked into the image; never retry the Hub
TRANSFORMERS_OFFLINE=1

# LLM
GROQ_API_KEY=...                 # optional in dev: topic extraction falls back to headings
GROQ_MODEL=openai/gpt-oss-120b
GROQ_REASONING_EFFORT=medium     # low can omit citations and cause false refusals
GROQ_TEMPERATURE=0.2
GROQ_MAX_TOKENS=1024
GROQ_TOPIC_MAX_TOKENS=4096
GROQ_TOPIC_BATCH_TOKENS=6000
GROQ_TOPIC_RETRIES=2

# Limits
MAX_UPLOAD_MB=50
MAX_FILES_PER_BATCH=20
MAX_PDF_PAGES=1000
ASSISTANT_QUERIES_PER_HOUR=20
CHUNK_TOKENS=600
CHUNK_OVERLAP_TOKENS=90

# Ingestion queue: auto | arq | inline | disabled
INGEST_QUEUE=auto

# Storage
STORAGE_BACKEND=local            # local | s3
UPLOAD_DIR=./data/uploads
S3_BUCKET=
S3_REGION=
```

## Appendix B — Release-gate checklist (for code review)

- [ ] New student-facing endpoint uses `StudentTopicView`/`StudentChunkView` (or an equally
      narrow DTO) and accepts `CourseScope`, never a raw `course_id`.
- [ ] Repository query joins `documents`, excludes soft-deleted rows, and includes
      `topics.release_state = 'released'` plus `chunks.released = true`.
- [ ] Any student Chroma query uses `student_vector_where(...)` (`released=true`).
- [ ] Student DTO does not serialize `correct_option`, `explanation`, `release_state`,
      `released_by`, `released_at`, chunk/vector metadata, or internal vector IDs.
- [ ] Release commits Postgres before touching Chroma and reports `chroma_synced`.
- [ ] Unrelease hides Chroma before committing Postgres and fails closed with `503`.
- [ ] No code path releases a topic automatically.
- [ ] Route added to the RBAC parametrized test matrix.
- [ ] Audit event emitted for any state change.
