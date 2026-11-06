# Security review

Scope: the single-node deployment described in `infra/docker-compose.yml`
(FastAPI API, ARQ worker, Next.js web, Postgres, Redis, Chroma) and the
application code in `backend/` and `web/`. Review date: 2026-11-05.

This is a development/demo deployment. The controls below are implemented and
verified; the "Remaining risks" section lists the gaps an operator must close
before exposing the system to the public internet.

## Reporting

Report suspected vulnerabilities privately to the maintainers rather than in a
public issue. Include the affected endpoint, a minimal reproduction, and the
impact you believe is possible. Do not test against data you do not own.

## Trust boundaries

| Boundary | Untrusted side | Enforced by |
|---|---|---|
| Browser ↔ web | Student/teacher/admin browser | Server layouts, middleware redirects, API authorization, CSP |
| Web ↔ API | Next.js server and browser requests | Same-origin cookies, CSRF double-submit, role checks |
| API ↔ Postgres | API process | Scoped repositories, foreign keys, parameterized SQLAlchemy |
| API ↔ Chroma | API/worker process | Metadata filter on `released` plus `course_id` |
| API ↔ Groq | API/worker process | Prompt isolation, bounded context, output validation |

The API is authoritative. Web-side role guards are a UX boundary; every API
route re-checks the session and course scope.

## Controls

### Authentication and sessions

- Passwords are hashed with Argon2id (`memory_cost=19456`, `time_cost=2`,
  `parallelism=2`) and transparently rehashed on login when parameters change.
- Access tokens are short-lived HS256 JWTs (`ACCESS_TOKEN_TTL_MIN`, default 15
  minutes). Refresh tokens are opaque 48-byte values; only their SHA-256 hashes
  are stored, and they rotate on every refresh.
- `lms_access`, `lms_refresh`, and `lms_role` cookies are `HttpOnly`,
  `SameSite=Lax`, `Path=/`; `Secure` is controlled by `COOKIE_SECURE` and must
  be `true` behind HTTPS.
- `lms_csrf` is intentionally readable by JavaScript so the client can echo it
  in `X-CSRF-Token`; the server compares both values in constant time.
- Logout clears all four cookies.
- Login is rate-limited per IP (5/minute) and per account (10/hour) with a
  Redis sliding window, so the limits are shared across API worker processes;
  both fall back to an in-process limiter when `REDIS_URL` is unset or Redis is
  unreachable. The study assistant is limited per student (20/hour, Redis
  counter with the same fallback). Limits return `Retry-After` and a
  problem+json body.

### Authorization

- Roles are `student`, `teacher`, `admin`. Route dependencies require an exact
  role (or teacher+admin where appropriate); admins bypass permission
  capabilities by design.
- `CourseScope` is derived from enrollments on every request. Repositories
  filter by course; out-of-scope resources return `404` rather than `403` so
  the API does not confirm existence.
- The release gate is enforced in three places: DTOs never serialize draft
  topics to students, SQL joins require `released_at IS NOT NULL` and
  `deleted_at IS NULL`, and Chroma queries carry a `released=true` metadata
  filter plus the course filter.
- The pytest authorization matrix covers student, teacher, and admin access to
  every mutating route; the RAG eval includes unreleased-document leak probes
  (leak rate 0.000).

### CSRF, XSS, and injection

- All unsafe HTTP methods require a matching `lms_csrf` cookie and
  `X-CSRF-Token` header, except login and the CSRF bootstrap endpoint.
- Uploads are validated by content type allow-list, non-empty body, the `%PDF-`
  magic bytes in the first KiB, a size cap (`MAX_UPLOAD_MB`), and a page cap
  (`MAX_PDF_PAGES`). Filenames are sanitized before storage.
- Database access uses SQLAlchemy expressions with bound parameters; no string
  concatenation of user input reaches SQL.
- React escapes all rendered strings. There is no `dangerouslySetInnerHTML`
  in the web app, so announcement bodies and citations are rendered as text.
- The Next.js app sets CSP (`default-src 'self'`, `frame-ancestors 'none'`),
  `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, and a
  restrictive `Permissions-Policy`.
- RAG prompts delimit retrieved context and instruct the model to ignore
  instructions inside documents; answers are rejected when citations do not
  validate. The eval suite includes a prompt-injection case (flagged 1/1).

### Secrets and configuration

- `.env` is gitignored; `.env.example` contains development placeholders only.
- A pattern scan for provider keys, AWS access keys, and private-key headers
  across `backend/`, `web/`, `scripts/`, `infra/`, and `load/` found no
  committed secrets.
- Production deployments must set a strong `SESSION_SECRET`, a non-default
  `SEED_ADMIN_PASSWORD`, `COOKIE_SECURE=true`, and a real `GROQ_API_KEY` via
  the environment or a secret store.

### Containers

- `api`, `worker`, and `web` run as unprivileged users (`app` uid 999, `node`
  uid 1000) with `read_only: true`, `cap_drop: [ALL]`,
  `no-new-privileges:true`, and tmpfs mounts for `/tmp` and framework caches.
- The only writable application path is the `uploads` volume; it is owned by
  the `app` user. Fresh volumes inherit that ownership from the image. Existing
  volumes created before the non-root change must be re-chowned once (see
  `docs/runbook.md`).
- Health checks gate startup ordering; `restart: unless-stopped` recovers from
  transient Redis/Chroma restarts.

### Audit and logging

- Mutations write append-only audit rows with actor, action, entity, and
  before/after payloads. Admins can view them at `/admin/audit`.
- Requests carry a request id in logs and problem+json responses. RAG query
  logs record retrieval counts, latency, refusal, and leak attempts.

## Verification evidence

| Check | Result |
|---|---|
| Backend authorization matrix (`pytest`) | Green, including role × route cases |
| Playwright e2e, three portals | 8/8 passing against the hardened stack (4.1 min, serial) |
| Backend gates | `ruff format --check` 136 files; `ruff check` clean; `mypy` 97 files clean; pytest 195 passed |
| Web gates | `tsc --noEmit` clean; `next build` succeeded (23 app routes incl. `_not-found`) |
| RAG eval (`eval/rag_cases.yaml`) | 41/41 cases PASS; leak rate 0.000 |
| Secret pattern scan | No findings |
| Container identity / filesystem | `id` shows uid 999 (api/worker) and 1000 (web); `/app` write attempts fail with `Read-only file system` |
| `docker compose config --quiet` | Passes |

## Dependency audit (2026-11-05)

`pip-audit` and `npm audit` were run against the pinned environments.

| Package | Severity | Advisory | Reachability / mitigation |
|---|---|---|---|
| `chromadb` 1.5.9 | High (5 CVEs) | PYSEC-2026-311, -3813, -3814, -3815 | Chroma runs as a private backend; only the API/worker call it, no untrusted client can create collections or supply model repositories. Do not expose port 8001 publicly. Track upstream fixes. |
| `pytest` 8.4.2 | Moderate | PYSEC-2026-1845 (CVE-2025-71176) | Dev/test tool installed for in-image quality gates; the CVE requires a hostile local user on a shared UNIX host. Containers are single-user and ephemeral. Upgrade requires raising the `<9.0` pin and revalidating pytest-asyncio. |
| `next` 15.5.28 | Moderate | npm advisory (range through 16.3 preview) | Fix is a semver-major upgrade to Next 16. Application does not use the affected image/optimizer path with untrusted input. Track upgrade. |
| `postcss` 8.5.x | High | GHSA-qx2v-qp2m-jg93, GHSA-6g55-p6wh-862q, GHSA-fxqj-rqcc-2cmp, GHSA-r28c-9q8g-f849 | Build-time dependency; attacker-controlled CSS/source maps are not accepted at runtime. Fixed by Next 16's dependency bump. |
| `zod` 3.x | High | GHSA-mrhx-c7xc-m3w5 | Installed but not imported by application code; no untrusted schema validation path. Remove or upgrade with the Next 16 change. |

No critical vulnerabilities were reported. The remaining findings are tracked
above rather than suppressed.

## Remaining risks and operator actions

1. **Chroma is exposed on host port 8001.** Bind it to `127.0.0.1` (or remove
   the published port) in any shared environment; it has no authentication of
   its own.
2. **TLS is terminated outside this compose file.** Set `COOKIE_SECURE=true`
   and serve web/API over HTTPS. The CSP allows inline scripts because Next.js
   injects a bootstrap script; tighten with nonces if the deployment adds a
   reverse proxy that can inject headers.
3. **Rate-limit state depends on Redis.** Login and assistant limits are shared
   across workers through Redis. If Redis is unavailable the API falls back to
   per-process counters, so limits are no longer global until Redis returns.
   Clear demo counters with `redis-cli --scan --pattern 'login:*' | xargs -r
   redis-cli del` (and the same for `assistant:rl:*`) rather than restarting the
   API.
4. **Dev extras are installed in the API image** so the documented quality
   gates can run inside the container. Build a slim production target without
   `pytest`, `ruff`, and `mypy` before shipping.
5. **The demo seed uses well-known passwords.** Change every seed credential
   and rotate `SESSION_SECRET` before any non-demo use.
6. **Uploads are stored on a local volume.** For multi-node deployments, move
   to object storage and add malware scanning; PDF parsing remains a
   memory/CPU boundary and should run with resource limits.

## Security checklist

- [x] Authz matrix green
- [x] CSRF on all mutations
- [x] Upload validation (type, magic bytes, size, pages, filename)
- [x] XSS-safe rendering (no raw HTML injection)
- [x] Login and assistant rate limits
- [x] Secrets not in the repository
- [x] Containers run non-root with a read-only root filesystem
- [x] Security headers and CSP
- [x] Append-only audit trail
- [x] Dependency audit run and findings documented (not suppressed)
