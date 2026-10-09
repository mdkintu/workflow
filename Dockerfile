# syntax=docker/dockerfile:1
# ADR-14: no Node in the runtime image. Tailwind's standalone CLI builds the
# CSS in its own stage; the app stage is plain python:3.12-slim.

ARG TAILWIND_VERSION=v4.1.14
ARG PYTHON_VERSION=3.12-slim

# ---- css: builds static/css/app.css with the pinned Tailwind standalone CLI ----
FROM debian:bookworm-slim AS css
ARG TAILWIND_VERSION
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY frontend/tailwind/ frontend/tailwind/
COPY frontend/field/ frontend/field/
COPY templates/ templates/
RUN curl -sL -o /usr/local/bin/tailwindcss \
      "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-x64" \
    && chmod +x /usr/local/bin/tailwindcss \
    && mkdir -p static/css \
    && tailwindcss -i frontend/tailwind/input.css -o static/css/app.css --minify

# ---- runtime: the one image every host runs (LAN or VPS; ADR-18) ----
FROM python:${PYTHON_VERSION} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --create-home app \
    && mkdir -p /data/media /data/static \
    && chown -R app:app /data
WORKDIR /app

COPY pyproject.toml manage.py ./
COPY workflow/ workflow/
COPY accounts/ accounts/
COPY organisations/ organisations/
COPY tasks/ tasks/
COPY checklists/ checklists/
COPY notifications/ notifications/
COPY dashboard/ dashboard/
COPY sync/ sync/
COPY templates/ templates/
COPY frontend/ frontend/
COPY static/ static/
COPY docker/entrypoint.sh /entrypoint.sh
COPY --from=css /build/static/css/app.css static/css/app.css

RUN pip install --no-cache-dir . \
    && chmod +x /entrypoint.sh \
    && chown -R app:app /app

USER app
ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "workflow.wsgi", "-b", "0.0.0.0:8000"]

# ---- dev: adds pytest/ruff/factory_boy for the `test` compose profile ----
FROM runtime AS dev
USER root
COPY tests/ tests/
RUN pip install --no-cache-dir .[dev] && chown -R app:app /app
USER app
