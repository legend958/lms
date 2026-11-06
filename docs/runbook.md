# Operations runbook

Single-node reference deployment in `infra/docker-compose.yml`: API, ARQ worker,
Next.js web, Postgres, Redis, Chroma. Commands use the compose file explicitly so
they do not depend on the working directory.

```powershell
$COMPOSE = "docker compose --env-file .env -f infra/docker-compose.yml"
```

## Service map

| Service | Container | Port | Writable state |
|---|---|---|---|
| `web` | `lms-web-1` | 3000 | none (read-only root; tmpfs `/tmp`, `.next/cache`) |
| `api` | `lms-api-1` | 8000 | `/data/uploads` volume only |
| `worker` | `lms-worker-1` | — | `/data/uploads` volume only |
| `postgres` | `lms-postgres-1` | 5432 | `pgdata` volume |
| `redis` | `lms-redis-1` | 6379 | `redisdata` volume |
| `chroma` | `lms-chroma-1` | 8001 → 8000 | `chromadata` volume |

Health: `GET /healthz` (liveness) and `GET /readyz` (Postgres, Redis, Chroma).
`docker compose ps` shows container health; `web` becomes healthy only after the
API is healthy.

## Deploy

```powershell
# 1. Build and start (migrations run as the api command)
$COMPOSE up -d --build

# 2. Verify
$COMPOSE ps
Invoke-RestMethod http://localhost:8000/readyz
Invoke-WebRequest http://localhost:3000/login -UseBasicParsing | Select-Object StatusCode
```

The API entrypoint runs `alembic upgrade head` before uvicorn, so a new image
migrates forward on start. Migrations are additive; never edit an applied
migration.

### Cold-start timing (clean database → demo ready)

Measured on the reference VM on 2026-11-06 with images already built:

| Phase | Command | Time |
|---|---|---|
| Tear down volumes | `$COMPOSE down -v --remove-orphans` | 13.6 s |
| Start stack, all healthy | `$COMPOSE up -d` | 69.4 s |
| Base seed | `$COMPOSE exec -T api python -m app.seed` | 5.5 s (74.9 s cumulative) |
| Demo dataset | `backend\.venv\Scripts\python.exe scripts\demo_seed.py` | 170.0 s (244.9 s cumulative) |
| **Total** | | **4.09 min** |

The demo seed waits for the API's background embedder warmup, ingests the three
PDFs, releases two topics, generates and publishes two quizzes, and submits six
attempts. Its own `elapsed` line reported 168.0 s. Results: 34 users, 3
documents, 20 topics (2 released), 2 quizzes, 6 attempts.

Image builds are not included; `$COMPOSE up -d --build` adds build time the
first time or after a dependency change. An earlier run on a loaded host took
6.22 min and failed at the second quiz because Groq's free-tier token window
rejected the request; the retry policy now waits out the provider's
`Retry-After` hint.

### Scaling the API

`API_WORKERS` controls the number of uvicorn workers (default `1`). Each worker
loads its own copy of the embedding model, so memory scales roughly linearly.
The reference VM (2.78 GiB) cannot host two workers: the second is OOM-killed.
See `docs/performance.md` for the measured ceiling and the recommended
remediation (more RAM or an out-of-process embedder).

## Rollback

There is no git remote in this workspace, so roll back by image tag rather than
by checkout.

```powershell
# Before an upgrade, keep the known-good images:
$COMPOSE build
docker tag lms-api:latest lms-api:rollback
docker tag lms-web:latest lms-web:rollback

# Roll back:
$COMPOSE down
docker tag lms-api:rollback lms-api:latest
docker tag lms-web:rollback lms-web:latest
$COMPOSE up -d --no-build
```

If a migration changed the schema and the old image cannot read it, restore the
pre-upgrade database backup (below) before starting the old image. Alembic
downgrades are not used in production.

## Backup

The database is the source of truth; Chroma is derived. Back up Postgres and the
uploads volume.

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

# Database (custom format, compressed)
$COMPOSE exec -T postgres pg_dump -U lms -Fc -f "/tmp/lms-$stamp.dump" lms
docker cp "lms-postgres-1:/tmp/lms-$stamp.dump" "backups/lms-$stamp.dump"
$COMPOSE exec -T postgres rm -f "/tmp/lms-$stamp.dump"

# Uploads volume
docker run --rm -v lms_uploads:/data -v "${PWD}/backups:/backup" alpine `
  tar -czf "/backup/uploads-$stamp.tar.gz" -C /data .
```

Keep at least one daily database dump and one uploads archive off-host.

## Restore drill

Restore into a scratch database, verify row counts, then drop it. This never
touches the live database.

```powershell
$stamp = "20261105-000000"   # the dump to verify
$COMPOSE exec -T postgres pg_restore -U lms -d postgres --create --clean --if-exists `
  --no-owner "/tmp/lms-$stamp.dump"   # adjust if the dump is on the host

# Safer explicit form when the dump is on the host:
docker cp "backups/lms-$stamp.dump" "lms-postgres-1:/tmp/lms-$stamp.dump"
$COMPOSE exec -T postgres createdb -U lms lms_restore_check
$COMPOSE exec -T postgres pg_restore -U lms -d lms_restore_check --no-owner "/tmp/lms-$stamp.dump"
$COMPOSE exec -T postgres psql -U lms -d lms_restore_check -t -c `
  "SELECT (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM courses) AS courses, (SELECT count(*) FROM documents) AS documents;"

# Clean up
$COMPOSE exec -T postgres dropdb -U lms lms_restore_check
```

Restore into the live database only during an incident:

```powershell
$COMPOSE stop api worker web
$COMPOSE exec -T postgres pg_restore -U lms -d lms --clean --if-exists --no-owner "/tmp/lms-$stamp.dump"
$COMPOSE start api worker web
```

**Last drill:** 2026-11-05 14:53 UTC — `pg_dump -Fc` produced a 160,758-byte
dump; `pg_restore` into `lms_restore_check` exited 0; row counts matched the
live database exactly (users 47, courses 2, documents 7, topics 39, chunks 64,
quiz attempts 8); the scratch database and in-container dump were dropped.

## Rotate secrets

```powershell
# 1. Generate new values
python -c "import secrets; print(secrets.token_urlsafe(48))"   # SESSION_SECRET

# 2. Update .env (SESSION_SECRET, SEED_ADMIN_PASSWORD, GROQ_API_KEY, POSTGRES_PASSWORD)
#    For Postgres, change the password in the database first:
$COMPOSE exec -T postgres psql -U lms -d lms -c "ALTER USER lms WITH PASSWORD 'new-password';"

# 3. Recreate the services that read the value
$COMPOSE up -d --force-recreate api worker web
```

Rotating `SESSION_SECRET` invalidates every access token; users must log in
again. Rotating `POSTGRES_PASSWORD` requires the matching `DATABASE_URL` value in
`.env` at the same time. Rotating `GROQ_API_KEY` only needs the api/worker
recreated.

## Reindex vectors

Chroma is derived. Re-run ingestion for a document to rebuild its chunks and
vectors:

- Teacher UI: **Documents → Retry**.
- API: `POST /api/v1/documents/{document_id}/retry` as the owning teacher or an
  admin.
- Admin UI: **Content → Reingest**.

Retry is safe: it resets the ingestion job and re-upserts the document's chunks.
Deleting a document removes its vectors through the admin content endpoint.

## Reconcile drift

Postgres is authoritative for `chunks.released`. The worker runs
`reconcile_vectors` hourly at minute 0. To run it now:

```powershell
$COMPOSE exec -T api python -c `
  "import asyncio; from app.services.ingestion_service import reconcile_vectors; print(asyncio.run(reconcile_vectors()))"
```

The return value is `(checked_topics, repaired_vectors)`. Repair direction is
always Postgres → Chroma, so a stale index can delay visibility but cannot grant
it early.

## Incidents

1. **Check readiness:** `Invoke-RestMethod http://localhost:8000/readyz`. The
   payload names the failing dependency.
2. **Check logs:** `$COMPOSE logs --tail=200 api worker web postgres redis chroma`.
3. **Check disk/memory:** `docker stats --no-stream`; `docker system df`.
4. **Restart one service:** `$COMPOSE restart api`. Avoid `down -v` unless you
   intend to delete state.
5. **API crash loop:** inspect `$COMPOSE logs api`. Repeated
   `Child process ... died` means the workers exceed container memory; set
   `API_WORKERS=1` and restart.
6. **Redis down:** login and assistant limits fall back to in-process counters
   (per worker). Sessions still work; the ARQ queue pauses. Restore Redis and
   restart `worker`.
7. **Chroma down:** ingestion and assistant retrieval fail; CRUD keeps working.
   Restart `chroma` and run the reindex/reconcile steps.
8. **Suspected leak:** capture the request id, the failing test/eval case, and
   `rag_query_logs`; stop the api; preserve the database before changing
   anything.
9. **Quiz generation fails with a Groq 429:** the free tier is 8k
   tokens/minute and a quiz request needs ~4k, so a quiz queued straight after
   topic extraction can exhaust the window. The worker honours the provider's
   `Retry-After` hint (capped at 30 s) across `GROQ_QUIZ_RETRIES` attempts
   (default 5). If it still fails, wait a minute and use the teacher UI to
   regenerate the quiz; raising the Groq tier removes the limit.
10. **Restore after data loss:** use the restore drill commands above with the
    most recent dump.

## Routine checks

| Cadence | Check |
|---|---|
| Every deploy | `readyz`, `docker compose ps`, web login page |
| Daily | Backup dump + uploads archive; disk usage |
| Weekly | `pip-audit` / `npm audit` review; log error scan |
| Monthly | Restore drill; secret rotation review; dependency upgrades |
| On call | `docs/performance.md` capacity table before raising load |

## Drill log

### 2026-11-05 — backup/restore

- Dump: `pg_dump -Fc` of the live demo database, 160,758 bytes
  (`backups/lms-20261105-145333.dump`), copied to the host and removed from the
  container.
- Restore target: scratch database `lms_restore_check` via
  `pg_restore --no-owner`; exit code 0.
- Verification: row counts identical to the live database — users 47, courses
  2, documents 7, topics 39, chunks 64, quiz attempts 8.
- Cleanup: scratch database dropped; in-container dump removed.
- Outcome: pass. The live database was not modified.

### 2026-11-06 — clean demo run

- Start: `$COMPOSE down -v --remove-orphans` deleted all four volumes.
- Stack healthy after 69.4 s; base seed done at 74.9 s.
- `scripts/demo_seed.py` exited 0 at 244.9 s (its own elapsed 168.0 s).
- Totals: 34 users, 3 documents, 20 topics (2 released), 2 quizzes, 6 attempts.
- Outcome: pass, 4.09 min end-to-end, under the 15-minute target.
