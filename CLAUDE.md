# CLAUDE.md — WorkFlow MVP

## What this is
WorkFlow is a task and checklist tool for **frontline and on-site teams in small and medium
businesses** (hotels and guest houses, shops, security and cleaning firms, and so on). Their
sites have patchy internet, costly mobile data and low-end Android phones. First market:
**Uganda**. We are competing with **WhatsApp groups and paper rosters**. `docs/WorkFlow.pdf`
is the original broad pitch; **it is not the spec**. The spec is `docs/01-requirements.md`.

## MVP scope — the complete list
1. **Tasks & shift rosters:** assign to a person, shift or location, with a due time, status and
   proof-of-completion photo.
2. **Recurring checklists** (opening/closing, inspections, rounds) generated on a schedule.
3. **Manager dashboard:** done / overdue / flagged, by staff, shift and location.
4. **Comments on tasks or checklist runs** (no general chat).
5. **SMS/WhatsApp reminders and overdue alerts.**

Plus the supporting pieces those five need: multi-tenant organisations; the Owner, Manager,
Supervisor and Staff roles; phone + PIN login; an offline PWA with sync; local hosting.

### Rule: do not add features outside the MVP list.
If a request (including one that seems to follow naturally from your own work) falls outside
the list above, **stop and ask** instead of building it. Explicitly out of scope: full
calendar, document storage, video, general messaging or chat, third-party integrations (and a
public API or webhooks), time and attendance, GPS tracking, inventory, native apps,
self-service sign-up and billing, and analytics beyond the dashboard counts. See
`docs/01-requirements.md` §7.

## Key documents
- `docs/01-requirements.md`: personas, user stories with acceptance criteria (story IDs such as
  F1.3 are used in test names), non-functional requirements, and open questions (§9; don't guess
  answers, ask).
- `docs/02-architecture.md`: components, tenancy, sync protocol and conflict rules, jobs,
  notifications, TLS, backups.
- `docs/04-design.md`: models, permission matrix, screens, URL map, sync API contract, state machine.
- `docs/03-decisions.md`: ADRs. **A new architectural choice or a new dependency needs a new
  ADR.**

## Stack (fixed; don't swap components)
Python 3.12 · Django 5 · HTMX + Alpine.js · Tailwind (standalone CLI, **no Node/npm**) · service
worker + Dexie.js · Django REST Framework (sync API only) · PostgreSQL 16 · Redis + Celery +
Celery Beat · Pillow · SMS through Africa's Talking behind `NotificationBackend` (console backend
in dev) · pytest-django + factory_boy + Playwright (Python) · ruff · Docker Compose + Caddy
(TLS mode from env: `internal` on a LAN, ACME on a VPS) + Gunicorn.

## Layout (planned)
`workflow/` (Django project: settings/{base,dev,prod}, urls, celery), apps at the repo root:
`accounts`, `organisations` (also the tenancy core: `tenancy.py`, `middleware.py`, `permissions.py`),
`tasks`, `checklists`, `notifications`, `dashboard`, `sync`. Plus `templates/`,
`frontend/{tailwind,field,vendor}`, `static/` (sw.js, manifest.json), `scripts/` (backup/restore),
`tests/`. Model details: `docs/04-design.md` §1.

## Conventions
**Tenancy (the most important rules)**
- Every tenant-owned model inherits `organisations.tenancy.TenantModel` (UUID PK, `organisation`,
  timestamps). Rows that phones pull inherit `SyncedTenantModel` (adds `updated_seq`, `deleted_at`).
- Use `Model.objects` (scoped; it raises if no organisation is set). **Never use `.unscoped` in
  views, serializers, forms or the sync app.** It is for migrations, admin scripts and Celery
  fan-out only.
- Get the organisation from `request.organisation` or from `tenant_context(org_id)` in Celery.
  Never take an organisation ID from user input.
- Every new tenant model, view or API endpoint gets a cross-tenant leak test in
  `tests/tenancy/`.
- Check that foreign keys between tenant records point to the same organisation.
- Never touch `Model.objects` of a tenant model at import time (module level, class bodies —
  e.g. a form field's `queryset=`): there's no organisation yet, so it raises and the app won't
  start. Use `queryset=None` on the field and set it in `__init__`.
  `tests/tenancy/test_manager.py::test_the_project_imports_without_a_tenant_context` guards this.
- Task visibility is `Task.objects.visible_to(membership)` ("mine"/"locs"/"all");
  another organisation's object is a 404, same organisation but not visible is a 403.

**Permissions:** use `can(membership, action, obj)` in `organisations.permissions` and the `visible_to(membership)`
querysets. Don't write ad-hoc role checks in templates or views.

**Offline and sync**
- Staff offline actions are limited to the mutation kinds in architecture §4.4. Adding a kind
  means updating the server handler, the client, the conflict rules and the tests together.
- Mutations are idempotent by client UUID. The server is authoritative (ADR-04).
- The field app stays within **≤ 250 KB of JS + CSS (compressed)**. Don't add JS dependencies
  without an ADR. Vendored libraries are pinned in `frontend/vendor/VERSIONS`.

**Checklist run status:** only `checklists/runs.py` changes `ChecklistRun.status` (plus the
generator creating runs and a paused schedule cancelling future ones, in `checklists/services.py`).

**Task status:** only `tasks/transitions.py` changes `Task.status` (`pending`, `in_progress`,
`done`, `flagged`, `cancelled`). Overdue is derived, never stored (ADR-17).

**Time:** store UTC and display in `organisation.timezone`. Keep `device_time` and
`received_at` on offline mutations.

**Celery:** tasks take IDs, not model instances, must be idempotent (backed by database
constraints), and set time limits.

**Notifications:** only through `notifications.backends.get_backend(channel)` and the dispatch
pipeline, which handles de-duplication, quiet hours and the budget. Never call a provider
directly. SMS templates must fit one 160-character GSM-7 segment.

**Photos:** re-encoded by Pillow on the server to ≤ 200 KB with EXIF stripped (`tasks/photos.py`),
and served only through the permission-checked `/media/p/<id>` view. The phone compresses too,
but only to save data.

**Host-agnostic (ADR-18):** no hostnames, IPs, ports, file paths or keys in code, templates or JS.
Everything host-specific comes from env (`.env.example` lists them all). Build absolute URLs from
`settings.SITE_URL`; the service worker and field app use relative URLs only. The same image must
run on the LAN and on a VPS with only `.env` changes.

**Privacy:** don't log phone numbers, PINs or message bodies. No third-party scripts, CDNs or
analytics; all data stays on the machine. The one exception is Google Fonts (ADR-19).

**UI:** use the design-system classes in `frontend/tailwind/input.css` (see `/styleguide/`) and
`{% icon %}` for Lucide icons (ADR-19, ADR-20). Mobile-first. Tap targets ≥ 44 px, text ≥ 16 px, icons as well as labels. All user-facing
strings are wrapped for translation (`gettext` / `{% translate %}`).

**Code style:** ruff for linting and formatting (config in `pyproject.toml`). Type hints on
service functions. Put business logic in `services.py` modules, not views.

**Tests:** new behaviour needs tests. Tests run in the organisations' default timezone
(Africa/Kampala; `tests/conftest.py`) so "today" in a test means the same as in the app. Name tests after the story they cover where possible
(`test_f1_3_complete_offline_...`).

## Commands
Copy `.env.example` to `.env` and fill in a real `SECRET_KEY` and `POSTGRES_PASSWORD` before
running any of this (LAN defaults otherwise; keep `DATABASE_URL`'s password in sync with
`POSTGRES_PASSWORD`). `make help` lists every target.

There are two ways to run the app day to day:

**Dev mode** — only `db` and `redis` run in Docker; Django, Celery and Tailwind run natively in
`.venv`. This is the normal day-to-day loop: faster reloads, real debugger, no image rebuilds.

```bash
make venv                                 # create .venv and pip install -e ".[dev]" (once)
make dev                                  # starts db+redis, migrates, then runserver in this terminal
make dev-seed                             # seed_demo, in another terminal
make dev-worker                           # Celery worker, in another terminal (needed for async jobs)
make dev-beat                             # Celery Beat, in another terminal (only if you need schedules)
make dev-css                              # Tailwind --watch, in another terminal (only if editing CSS)
make dev-down                             # stop the db+redis containers when you're done

make test-local                           # pytest natively (starts db+redis if not already up)
make e2e-local                            # Playwright end-to-end (offline field app) with system Chrome
make lint-local                           # ruff check + ruff format --check natively
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py createsuperuser
```

`make dev`'s login page is at `http://localhost:8000/login/` (plain HTTP — no Caddy/TLS in dev
mode; browsers treat `localhost` as a secure context regardless, so the service worker still
registers). `scripts/dev-env.sh` is what points the native processes at the containers'
published ports (`DB_PORT`/`REDIS_PORT` in `.env`, default 5432/6379) without touching `.env`'s
own `DATABASE_URL`/`REDIS_URL` — those stay `db`/`redis` for the full stack and for a VPS
deploy (ADR-18).

**Full stack** — everything in Docker, behind Caddy, closest to how it actually deploys.

```bash
make up                                   # build + start the full stack (caddy, web, worker, beat, db, redis)
make down                                 # stop the stack
make logs                                 # tail web + worker logs
make migrate                              # run Django migrations in the web container
make seed                                 # run the seed_demo management command
make shell                                # Django shell in the web container
make test                                 # pytest, in the 'test' compose profile, against real db/redis
make lint                                 # ruff check + ruff format --check, same profile
make css                                  # (re)build static/css/app.css once with the pinned Tailwind CLI
make backup                               # scripts/backup.sh: pg_dump + media/caddy_data archive + checksums
make restore DATE=YYYYMMDD-HHMM           # scripts/restore.sh: restore a backup (stops app services first)

docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py check --deploy
docker compose exec web pytest -m e2e     # Playwright end-to-end (slower; browsers not installed by default)
```

## Working agreements for Claude
- Planning documents come before code. If code would contradict `docs/`, update the documents
  (or ask) first.
- Open questions in `docs/01-requirements.md` §9 use their stated defaults. Don't invent other
  answers; ask.
- Keep changes small and focused. Don't refactor unrelated code or add "nice to have" features.
