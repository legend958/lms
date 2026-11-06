#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ARGS=(-f infra/docker-compose.yml)
if [[ -f .env ]]; then
  ARGS=(--env-file .env "${ARGS[@]}")
fi

command="${1:-up}"

case "$command" in
  up)
    docker compose "${ARGS[@]}" up -d --build
    echo "Web: http://localhost:3000  API: http://localhost:8000/docs"
    ;;
  down) docker compose "${ARGS[@]}" down ;;
  migrate) docker compose "${ARGS[@]}" exec api alembic upgrade head ;;
  seed) docker compose "${ARGS[@]}" exec api python -m app.seed ;;
  test) docker compose "${ARGS[@]}" exec api pytest -q ;;
  lint)
    docker compose "${ARGS[@]}" exec api ruff check app tests
    docker compose "${ARGS[@]}" exec api ruff format --check app tests
    docker compose "${ARGS[@]}" exec api mypy app
    docker compose "${ARGS[@]}" exec web npm run lint
    ;;
  fmt)
    docker compose "${ARGS[@]}" exec api ruff format app tests
    docker compose "${ARGS[@]}" exec api ruff check --fix app tests
    ;;
  logs) docker compose "${ARGS[@]}" logs -f --tail=100 ;;
  ps) docker compose "${ARGS[@]}" ps ;;
  build) docker compose "${ARGS[@]}" build ;;
  *)
    echo "Usage: $0 {up|down|migrate|seed|test|lint|fmt|logs|ps|build}" >&2
    exit 1
    ;;
esac
