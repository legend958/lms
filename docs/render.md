# Deploy on Render

Render builds each Docker service in an isolated environment that **cannot
reach your private Postgres or Chroma services**, and it does not mount a
service's persistent disk during the build. A vector rebuild therefore cannot
run in the build step. Render's **Pre-Deploy Command** is the correct hook: it
runs after the image is built, before the new instance receives traffic, with
the service's environment and private-network access.

The API image already contains the embedding model, so the pre-deploy rebuild
needs no PDFs and no downloads.

## Services

Create these in the same **region** (private networking requires it).

### 1. Chroma (Private Service)

| Field | Value |
|---|---|
| Type | Private Service |
| Source | Existing Image |
| Image | `chromadb/chroma:1.5.9` |
| Root Directory | *(blank)* |
| Build Command | *(not applicable)* |
| Docker Command | `run --path /data --host 0.0.0.0 --port 8000` |
| Disk | mount `/data`, 5–10 GB |
| Environment | `IS_PERSISTENT=TRUE`, `ANONYMIZED_TELEMETRY=FALSE`, `PORT=8000` |

The Docker Command must **not** start with `/bin/sh -c`. This image's
entrypoint is `dumb-init -- chroma`, so the command is appended to it as
`chroma run ...`. The internal address is `chroma-xxxx:8000`; use the hostname
from the service's **Connect** menu as `CHROMA_HOST` on the API.

### 2. API (Web Service)

| Field | Value |
|---|---|
| Type | Web Service |
| Runtime | Docker |
| Root Directory | `backend` |
| Dockerfile Path | `Dockerfile` |
| Build Command | *(leave empty; Docker services have no build command)* |
| Docker Command | `/bin/sh -c "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers ${API_WORKERS:-1} --no-access-log"` |
| Pre-Deploy Command | `/bin/sh -c "alembic upgrade head && python -m app.cli rebuild-vectors"` |
| Health Check Path | `/readyz` |
| Disk | mount `/data`, 5 GB (original PDFs) |
| Plan | `1c-2g` minimum; `2c-4g` for ingestion |

`rebuild-vectors` re-embeds every chunk stored in Postgres and clears stale
vectors first. An empty database is a no-op, so the first deploy does not erase
anything.

### 3. Web (Web Service)

| Field | Value |
|---|---|
| Type | Web Service |
| Runtime | Docker |
| Root Directory | `web` |
| Dockerfile Path | `Dockerfile` |
| Build Command | *(leave empty)* |
| Docker Command | `/bin/sh -c "exec npm start -- -p ${PORT:-10000}"` |
| Health Check Path | `/login` |
| Environment | `API_INTERNAL_URL=https://<api-service>.onrender.com` |

`API_INTERNAL_URL` is evaluated during `next build`, so it must be present on
the **build**, not only at runtime. Use the API's public URL; the Next server
proxies `/api/*` to it.

## API environment variables

| Key | Value |
|---|---|
| `APP_ENV` | `production` |
| `SESSION_SECRET` | 32+ random characters (not `dev-only-change-me`) |
| `COOKIE_SECURE` | `true` |
| `DATABASE_URL` | Render Postgres **Internal Database URL** (paste as-is; `postgresql://` is normalized to `postgresql+asyncpg://`) |
| `REDIS_URL` | Optional: Render Key Value internal URL |
| `ALLOWED_ORIGINS` | `https://<web-service>.onrender.com` |
| `CHROMA_HOST` | Chroma private-service hostname, e.g. `chroma-abc1` |
| `CHROMA_PORT` | `8000` |
| `CHROMA_COLLECTION` | `lms_chunks` |
| `UPLOAD_DIR` | `/data/uploads` |
| `INGEST_QUEUE` | `inline` (see note below) |
| `GROQ_API_KEY` | Groq API key |
| `GROQ_MODEL` | `openai/gpt-oss-120b` |
| `GROQ_REASONING_EFFORT` | `medium` |
| `GROQ_QUIZ_RETRIES` | `5` |
| `API_WORKERS` | `1` |
| `SEED_ADMIN_PASSWORD` | Strong password for the first admin |
| `WEB_BASE_URL` | `https://<web-service>.onrender.com` |
| `API_BASE_URL` | `https://<api-service>.onrender.com` |

`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` are already baked into the API
image. Keep them if you override the Dockerfile.

`INGEST_QUEUE=inline` is required with local disk storage. An ARQ worker cannot
read the API service's disk (a Render disk belongs to one service). Use `arq`
only if you move uploads to S3 and run a worker.

## First deploy order

1. **Postgres** — create the database and copy its Internal Database URL.
2. **Key Value** — optional; set `REDIS_URL` if created.
3. **Chroma** — wait for the private service to be live, then copy its hostname.
4. **API** — set all environment variables and the pre-deploy command. The
   build bakes MiniLM and installs `libgomp1`; expect 10–20 minutes.
5. **Seed** — run once from the API's Render Shell:
   ```bash
   python -m app.seed
   ```
6. **Web** — set `API_INTERNAL_URL` to the API URL and deploy.
7. **Corpus** — the 13 textbook PDFs are not in git. Sign in as a teacher and
   upload them through the UI (or copy them to the API disk with SCP and run
   `python -m app.cli ingest`). Ingestion runs in-process and writes chunks to
   Postgres and vectors to Chroma.

After the corpus is ingested, every later API deploy runs
`rebuild-vectors` automatically before traffic switches. If the Chroma disk is
ever replaced, trigger an API deploy (or run the command in the Shell) and the
index is rebuilt from Postgres without the PDFs.
