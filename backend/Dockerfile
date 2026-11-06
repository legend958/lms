# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models

WORKDIR /app

# libgomp is required by the local embedding model.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install third-party dependencies before the application code. Editing a file
# under app/ then only invalidates the cheap wheel build below, instead of
# re-downloading torch and friends. The BuildKit cache mount keeps wheels
# between builds even when this layer is invalidated by a pyproject change.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python - <<'PY' > /tmp/requirements.txt
import tomllib

project = tomllib.load(open("pyproject.toml", "rb"))["project"]
print("\n".join(project["dependencies"] + project["optional-dependencies"]["dev"]))
PY
# Dev extras keep the documented `docker compose exec api pytest|ruff|mypy`
# quality gates runnable inside the same image.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/requirements.txt

# Bake MiniLM into the image so workers never download weights at runtime.
# This sits above the source copy so code changes do not re-download weights.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# Weights are baked above. Never let a runtime process try the Hub: the retry
# backoff added minutes to the first embedding load when egress was restricted.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY app ./app
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps .

COPY alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests
COPY eval ./eval

# Run as an unprivileged user. The uploads volume is the only writable data
# path at runtime; the model cache and source tree stay read-only.
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /home/app --create-home app \
    && mkdir -p /data/uploads \
    && chown -R app:app /app /data /models /home/app

USER app

ENV HOME=/home/app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
