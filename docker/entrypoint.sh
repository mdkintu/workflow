#!/bin/bash
# Entrypoint for the `web`/`worker`/`beat`/`dev` containers (all share one
# image; ADR-18). Only the `web` role (gunicorn) runs migrate + collectstatic
# before exec'ing the real command, so worker/beat don't race it.
set -euo pipefail

if [ "${1:-}" = "gunicorn" ]; then
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
fi

exec "$@"
