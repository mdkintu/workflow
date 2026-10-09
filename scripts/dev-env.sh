#!/usr/bin/env bash
# Native "dev mode" wrapper (CLAUDE.md "Commands"; the `dev-*` Makefile
# targets). `make dev` runs only `db`/`redis` in Docker; Django, Celery and
# Tailwind run natively in .venv. Those two containers publish their ports to
# 127.0.0.1 (docker-compose.yml), so native processes reach them at
# localhost:$DB_PORT / localhost:$REDIS_PORT instead of the `db`/`redis`
# hostnames only the Docker network can resolve.
#
# This sources `.env` for everything else (SMS_BACKEND, MEDIA_ROOT, ...) but
# does NOT touch `.env`'s own DATABASE_URL/REDIS_URL — those stay pointed at
# `db`/`redis` for `make up` and a VPS deploy (ADR-18: only `.env` changes
# between hosts). The rewrite happens only in this process's environment.
#
# Usage: ./scripts/dev-env.sh <command> [args...]
#   ./scripts/dev-env.sh .venv/bin/python manage.py migrate
#   ./scripts/dev-env.sh .venv/bin/celery -A workflow worker -Q default,notifications

set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <command> [args...]" >&2
  exit 2
fi

cd "$(dirname "$0")/.."

set -a
[ -f .env ] && . ./.env
set +a

: "${POSTGRES_USER:?POSTGRES_USER not set — copy .env.example to .env first}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD not set — copy .env.example to .env first}"
: "${POSTGRES_DB:?POSTGRES_DB not set — copy .env.example to .env first}"

export DJANGO_SETTINGS_MODULE="workflow.settings.dev"
# .env says DEBUG=false (it's for the Docker stack). runserver only serves
# /static/ (CSS, htmx, Alpine) with DEBUG on, so dev mode needs it on.
export DEBUG=true
export DATABASE_URL="postgres://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${DB_PORT:-5432}/${POSTGRES_DB}"
export REDIS_URL="redis://localhost:${REDIS_PORT:-6379}/0"
# So the native runserver/tests accept requests to either name.
export ALLOWED_HOSTS="localhost,127.0.0.1"
# .env's MEDIA_ROOT/STATIC_ROOT are container paths, and there's no Caddy in
# dev mode to take an X-Accel-Redirect — so files live in the checkout and
# Django streams photos itself.
export MEDIA_ROOT="$PWD/mediafiles"
export STATIC_ROOT="$PWD/staticfiles"
export MEDIA_X_ACCEL=false

# Optional: serve dev mode through an HTTPS tunnel for a demo, e.g.
#   TUNNEL_HOST=abc.trycloudflare.com make dev
# The tunnel terminates TLS, so trust its X-Forwarded-Proto header.
if [ -n "${TUNNEL_HOST:-}" ]; then
  export ALLOWED_HOSTS="$ALLOWED_HOSTS,$TUNNEL_HOST"
  export SITE_HOST="$TUNNEL_HOST"
  export SITE_URL="https://$TUNNEL_HOST"
  export CSRF_TRUSTED_ORIGINS="https://$TUNNEL_HOST"
  export TRUST_PROXY_HEADERS=true
fi

exec "$@"
