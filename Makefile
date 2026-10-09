COMPOSE ?= docker compose
TAILWIND_VERSION ?= v4.1.14
DEV_ENV := ./scripts/dev-env.sh

.PHONY: help up down logs migrate seed shell test lint css backup restore \
        venv dev dev-db dev-down dev-migrate dev-seed dev-worker dev-beat dev-css \
        test-local e2e-local lint-local

help:
	@echo "Full stack (Docker: caddy, web, worker, beat, db, redis):"
	@echo "  up           Build and start the full stack"
	@echo "  down         Stop the stack"
	@echo "  logs         Tail web + worker logs"
	@echo "  migrate      Run Django migrations in the web container"
	@echo "  seed         Run the seed_demo management command"
	@echo "  shell        Open a Django shell in the web container"
	@echo "  test         Run pytest in the 'test' compose profile"
	@echo "  lint         Run ruff check + ruff format --check, same profile"
	@echo "  backup       Run scripts/backup.sh"
	@echo "  restore      Run scripts/restore.sh DATE=YYYYMMDD-HHMM"
	@echo ""
	@echo "Dev mode (only db+redis in Docker; Django/Celery/Tailwind native, .venv):"
	@echo "  dev          Start db+redis, migrate, then run runserver in the foreground"
	@echo "  dev-worker   Run a Celery worker natively (separate terminal)"
	@echo "  dev-beat     Run Celery Beat natively (separate terminal)"
	@echo "  dev-css      Rebuild Tailwind CSS on change (separate terminal)"
	@echo "  dev-migrate  Run migrations natively"
	@echo "  dev-seed     Run seed_demo natively"
	@echo "  dev-db       Start just the db+redis containers"
	@echo "  dev-down     Stop the db+redis containers"
	@echo "  test-local   Run pytest natively (starts db+redis if needed)"
	@echo "  e2e-local    Run the Playwright end-to-end tests natively (system Chrome)"
	@echo "  lint-local   Run ruff natively"
	@echo ""
	@echo "  css          Rebuild static/css/app.css once with the pinned Tailwind CLI"

# --- Full stack ---

up:
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f web worker

migrate:
	$(COMPOSE) exec web python manage.py migrate

seed:
	$(COMPOSE) exec web python manage.py seed_demo

shell:
	$(COMPOSE) exec web python manage.py shell

# --build: the test image is separate from the app image and `up` doesn't
# rebuild it, so without this `make test` would test stale code.
test:
	$(COMPOSE) --profile test run --rm --build test

lint:
	$(COMPOSE) --profile test run --rm --build test sh -c "ruff check . && ruff format --check ."

backup:
	./scripts/backup.sh

restore:
	./scripts/restore.sh $(DATE)

# --- Dev mode: db+redis in Docker (published to 127.0.0.1), everything else
# native in .venv. scripts/dev-env.sh points DATABASE_URL/REDIS_URL at those
# published ports without touching .env's own values (ADR-18; see its header
# comment). Needs `.env` (copy .env.example) and `make venv` once. ---

venv: .venv/bin/pytest

.venv/bin/pytest:
	python3 -m venv .venv
	./.venv/bin/pip install --upgrade pip
	./.venv/bin/pip install -e ".[dev]"

dev-db:
	$(COMPOSE) up -d --wait db redis

dev-down:
	$(COMPOSE) stop db redis

dev-migrate: venv dev-db
	$(DEV_ENV) .venv/bin/python manage.py migrate

dev-seed: venv dev-db
	$(DEV_ENV) .venv/bin/python manage.py seed_demo

dev: dev-migrate
	$(DEV_ENV) .venv/bin/python manage.py runserver 0.0.0.0:8000

dev-worker: venv dev-db
	$(DEV_ENV) .venv/bin/celery -A workflow worker -Q default,notifications -l info

dev-beat: venv dev-db
	$(DEV_ENV) .venv/bin/celery -A workflow beat -l info

dev-css: bin/tailwindcss
	./bin/tailwindcss -i frontend/tailwind/input.css -o static/css/app.css --watch

test-local: venv dev-db
	$(DEV_ENV) .venv/bin/pytest

# Uses the installed Google Chrome, so no Playwright browser download is needed.
e2e-local: venv dev-db
	$(DEV_ENV) .venv/bin/pytest -m e2e --browser-channel chrome

lint-local: venv
	. .venv/bin/activate && ruff check . && ruff format --check .

css: bin/tailwindcss
	./bin/tailwindcss -i frontend/tailwind/input.css -o static/css/app.css --minify

bin/tailwindcss:
	mkdir -p bin
	curl -sL -o bin/tailwindcss "https://github.com/tailwindlabs/tailwindcss/releases/download/$(TAILWIND_VERSION)/tailwindcss-linux-x64"
	chmod +x bin/tailwindcss
