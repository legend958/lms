# API reference

Base URL: `http://localhost:8000/api/v1` (development). Interactive schema:
`http://localhost:8000/docs`, machine-readable schema:
`http://localhost:8000/openapi.json`.

Health endpoints are unversioned: `GET /healthz` (liveness) and `GET /readyz`
(readiness: Postgres, Redis, Chroma).

## Authentication

The API issues cookies; there are no bearer tokens in the browser flow.

| Cookie | Purpose | Flags |
|---|---|---|
| `lms_access` | Short-lived HS256 JWT access token | `HttpOnly`, `SameSite=Lax`, `Secure` when `COOKIE_SECURE=true` |
| `lms_refresh` | Opaque rotating refresh token (hash stored server-side) | `HttpOnly`, `SameSite=Lax` |
| `lms_role` | Role hint used by the web server layouts | `HttpOnly`, `SameSite=Lax` |
| `lms_csrf` | CSRF token that JavaScript must echo | readable by JS |

```http
POST /api/v1/auth/login
Content-Type: application/json

{"email": "student01@lms.example.com", "password": "Passw0rd!"}
```

`POST /auth/refresh` rotates the refresh token and reissues the access token.
`GET /auth/me` returns the current user, role, and capabilities.
`POST /auth/logout` clears all four cookies. `GET /auth/csrf` bootstraps the CSRF
cookie for a new browser session.

### CSRF

Every unsafe method (`POST`, `PATCH`, `DELETE`) requires the `lms_csrf` cookie
and a matching `X-CSRF-Token` header. Login and `GET /auth/csrf` are exempt.

## Errors

Errors are RFC 7807 problem+json:

```json
{
  "type": "https://lms/errors/validation",
  "title": "Validation failed",
  "status": 422,
  "detail": "One or more fields were invalid.",
  "request_id": "0f0f...",
  "errors": [{"loc": ["body", "email"], "msg": "value is not a valid email address"}]
}
```

| Status | Meaning |
|---|---|
| 401 | Missing/expired session |
| 403 | CSRF failure or wrong role for a portal-only route |
| 404 | Not found **or** out of course scope (the API does not confirm existence) |
| 409 | State conflict (duplicate enrollment, already published, etc.) |
| 413 | Upload exceeds `MAX_UPLOAD_MB` |
| 422 | Request validation |
| 429 | Rate limited; includes `Retry-After` |
| 500 | Unhandled server error; safe detail only, `request_id` for correlation |

## Pagination

Cursor-paginated collections use:

```json
{"items": [], "next_cursor": "opaque-or-null"}
```

Pass `cursor=<next_cursor>` to fetch the next page. Some list endpoints accept
`limit` (default 50, max 100) and filters such as `status`, `from`, and `to`.

## Endpoints

### Auth

| Method | Path | Roles |
|---|---|---|
| POST | `/auth/login` | public |
| POST | `/auth/logout` | any |
| POST | `/auth/refresh` | any |
| GET | `/auth/csrf` | public |
| GET | `/auth/me` | any |

### Courses, enrollments, announcements, notifications

| Method | Path | Roles |
|---|---|---|
| GET | `/courses` | any (scoped) |
| POST | `/courses` | admin |
| GET, PATCH, DELETE | `/courses/{id}` | member / admin |
| GET | `/courses/{id}/enrollments` | teacher of course / admin |
| POST | `/courses/{id}/enrollments` | teacher of course / admin |
| DELETE | `/courses/{id}/enrollments/{user_id}` | teacher of course / admin |
| POST | `/courses/{id}/self-enroll` | student |
| GET | `/announcements` | any (scoped) |
| POST | `/announcements` | teacher / admin |
| PATCH, DELETE | `/announcements/{id}` | author / admin |
| POST | `/announcements/{id}/read` | student |
| GET | `/notifications` | any |
| POST | `/notifications/{id}/read` | any |

### Documents and topics

| Method | Path | Roles |
|---|---|---|
| POST | `/documents` (multipart) | teacher / admin |
| GET | `/documents`, `/documents/{id}`, `/documents/{id}/status` | teacher / admin |
| POST | `/documents/{id}/retry` | teacher / admin |
| DELETE | `/documents/{id}` | teacher / admin |
| GET | `/documents/{id}/topics` | teacher / admin |
| GET | `/topics/{id}/preview` | teacher / admin |
| PATCH, DELETE | `/topics/{id}` | teacher / admin |
| POST | `/topics/{id}/release`, `/topics/{id}/unrelease` | teacher / admin |
| POST | `/topics/{id}/split`, `/topics/merge` | teacher / admin |

### Teacher quizzes, analytics, student learning

| Method | Path | Roles |
|---|---|---|
| GET | `/teacher/quizzes`, `/teacher/quizzes/{id}` | teacher of course / admin |
| POST | `/teacher/topics/{id}/quiz` | teacher / admin |
| POST | `/teacher/quizzes/{id}/approve`, `/regenerate` | teacher / admin |
| PATCH, DELETE | `/teacher/questions/{id}` | teacher / admin |
| GET | `/teacher/analytics`, `/students`, `/students/{id}`, `/topics` | teacher of course / admin |
| GET | `/student/dashboard`, `/tasks`, `/topics`, `/topics/{id}` | student |
| PATCH | `/student/tasks/{id}` | student |
| GET | `/student/quizzes`, `/student/quizzes/{id}` | student |
| POST | `/student/quizzes/{id}/attempts` | student |
| GET, PATCH | `/student/attempts/{id}` | student |
| POST | `/student/attempts/{id}/submit` | student |
| GET | `/student/attempts/{id}/result` | student |

### Admin

| Method | Path |
|---|---|
| GET, POST | `/admin/users` |
| PATCH, DELETE | `/admin/users/{id}` |
| POST | `/admin/users/import` (CSV, multipart) |
| GET | `/admin/audit-logs` |
| GET | `/admin/content`, `/admin/content/{id}/download` |
| POST | `/admin/content/{id}/reingest` |
| DELETE | `/admin/content/{id}` |
| GET, PATCH | `/admin/permissions` |
| GET | `/admin/system/health` |

Admins bypass teacher/student capability checks by design. Every route still
re-validates the session, role, and course scope.

## Study assistant (SSE)

`POST /student/ask` streams `text/event-stream`:

```json
{"question": "Summarise the key idea...", "course_id": "uuid", "topic_id": "uuid"}
```

Events, in order:

| Event | Data |
|---|---|
| `token` | `{"text": "..."}` — answer fragments |
| `citations` | `{"citations": [{"document_id", "page", "snippet"}], "refusal": false}` |
| `error` | `{"code": "...", "message": "..."}` — only on generation failure |
| `done` | `{"latency_ms": 1234}` — always last |

Retrieval is restricted to chunks from released topics in the requested course.
When nothing relevant is released, the stream ends with `refusal: true` and no
citations. The stream always ends with `done`, including after an `error`.

## Rate limits

| Scope | Limit | Key |
|---|---|---|
| Login | 5/minute per IP | Redis sliding window |
| Login | 10/hour per account | Redis sliding window |
| Study assistant | 20/hour per student (`ASSISTANT_QUERIES_PER_HOUR`) | Redis counter |

Limits are shared across API worker processes when `REDIS_URL` is set and fall
back to an in-process limiter when Redis is unavailable. A `429` includes
`Retry-After` in seconds.

## Conventions

- All timestamps are ISO 8601 UTC.
- IDs are UUIDv7 strings.
- Mutations on teacher/admin resources write an audit row with actor, action,
  entity, and before/after payloads.
- Student DTOs never include `correct_option`, `explanation`, or internal
  release state.
- `GET /student/topics` and every student read path apply the release gate in
  SQL and (for RAG) in the Chroma metadata filter.
