COMPOSE ?= docker compose --env-file .env -f infra/docker-compose.yml

.PHONY: up down migrate seed test lint fmt logs ps build corpus-check

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

migrate:
	$(COMPOSE) exec api alembic upgrade head

seed:
	$(COMPOSE) exec api python -m app.seed

test:
	$(COMPOSE) exec api pytest -q

lint:
	$(COMPOSE) exec api ruff check app tests
	$(COMPOSE) exec api ruff format --check app tests
	$(COMPOSE) exec api mypy app
	$(COMPOSE) exec web npm run lint

fmt:
	$(COMPOSE) exec api ruff format app tests
	$(COMPOSE) exec api ruff check --fix app tests

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

build:
	$(COMPOSE) build

corpus-check:
	python scripts/verify_corpus.py
