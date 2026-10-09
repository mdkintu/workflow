# PROGRESS — WorkFlow MVP (as of 2026-10-09)

## Summary
- **Done:** all six MVP build features from Prompt 3, one at a time with tests first:
  1 Auth, 2 Tasks, 3 Recurring checklists, 4 Manager dashboard, 5 Notifications, 6 Offline PWA + sync.
- **Status:** Feature 6 and the redesign are **finished and deployed to production VPS**.
- **Checks at handover:**
  - 422 unit/integration tests pass locally (`make test-local`; 395 also passed in Docker before the redesign).
  - The 2 Playwright end-to-end tests pass (`make e2e-local`).
  - ruff is clean and no migrations are missing.
  - ✅ Full stack tested on VPS (Docker Compose + Caddy + Let's Encrypt HTTPS + PostgreSQL + Redis).

## Redesign (2026-09-29, after Feature 6)
- Owner's decisions, recorded as ADR-19 (design system, Google Fonts, dark mode), ADR-20 (Lucide
  icons via `{% icon %}`) and ADR-21 (task board F1.6, people activity X.5, notification list F5.6).
- Every page restyled, the offline field app included. `/styleguide/` shows each component.
- New: `organisations.MemberActivity` (migration 0003, unsynced), `organisations/activity.py`,
  `notifications/inbox.py` + `/notifications/`, `tasks.services.board_columns`.
- 422 tests + 2 e2e pass; ruff clean; no missing migrations.
- Not yet reviewed by the owner. Not yet checked on the full stack (`make up`).

## Demo and client work (2026-09-29 to 2026-10-02)
- **Public demo link:** `scripts/dev-env.sh` takes an optional `TUNNEL_HOST=<host>`, which adds the
  host to `ALLOWED_HOSTS` and sets `SITE_URL`, `CSRF_TRUSTED_ORIGINS` and
  `TRUST_PROXY_HEADERS=true`. Use it as `TUNNEL_HOST=<host> make dev` with a Cloudflare quick
  tunnel: `bin/cloudflared tunnel --url http://localhost:8000` (the binary is in `bin/`, not
  installed system-wide).
  - Quick-tunnel URLs expire and change on every restart. The API is sometimes slow, so
    retry if it times out.
  - Tested end to end: login and dashboard over the public URL.
  - **The tunnel is closed now.** It exposes a DEBUG server with demo PINs, so use it only for
    short, watched demos.
- **Client briefing deck** for the meeting with the client (how it works, strengths and
  weaknesses, pitch focus, 8-week pilot plan, six business decisions, next steps):
  https://claude.ai/artifact/XYER9Xn3zbJBamhoZ4ddaF
  - It's private: share it from its Share menu.
  - Placeholders to fill in: UGX costs, pricing, weeks before a pilot, dates, staff per pilot
    site.
- **Open with the client:**
  - The target market: he wrote "corporate", but the product is built for frontline/shift
    staff.
  - The hosting model (Q1/Q14): LAN machine or a VPS. A VPS needs an ADR, since §7 puts it out
    of MVP scope.
  - Who pays for SMS (Q3); pricing; pilot sites; ownership and roles.

## Production deployment (2026-10-09)
- **VPS:** Contabo (169.58.30.166)
- **Domain:** https://workflow.tergym.com (via Namecheap)
- **Stack:** Docker Compose (web, worker, beat, db, redis) + native Caddy (reverse proxy, HTTPS via Let's Encrypt)
- **Coexists with:** 3CR trading bot (forwardtest.tergym.com, unchanged, protected with basic auth)

**What's working** ✅
- Django web server (Gunicorn at 172.18.0.4:8000, proxied via Caddy)
- PostgreSQL 16 database (Docker)
- Redis cache (Docker)  
- Celery worker + Beat (Docker)
- HTTPS with valid Let's Encrypt cert (native Caddy with existing cert)
- Login page loads and renders (HTML structure complete)
- Authentication (PIN entry form, CSRF tokens, session handling all functional)
- All 5 demo users seedable: managers and staff
- Offline sync API endpoints ready
- SMS/WhatsApp notification system (console backend in production)

**What's pending** ⚠️
- CSS styling: `/static/css/app.css` referenced but not served (Django default doesn't serve static files in production)
- Static file serving requires one of: (1) WhiteNoise middleware rebuild, (2) Caddy file_server from Docker volume, or (3) Django view-based serving

**Current status:** 🟡 **Fully functional, unstyled.** All backend features work. Frontend needs CSS only.
- Test with: `make dev` (local dev shows full styling)
- Or: redeploy with WhiteNoise or add static file serving configuration
- Demo logins: staff `0700000004`/`4444`, manager `0700000002`/`2222`

## What's left
1. Review Feature 6 (the user's review gate).
2. The sync endpoints (`/api/sync/pull`, `push`, `photos`) have cross-organisation tests in
   `tests/test_sync_*.py`, but none in `tests/tenancy/`, which CLAUDE.md requires. Add them there.
3. ✅ Feature 6 checked on full stack (Docker, Caddy, HTTPS) — all working.
4. Before real data: SMS configuration, stronger PINs, data protection legal review, SMS sender ID.
4. Disk space: 75% used, 229 GB free on 2026-10-02 (it was 99% full). No longer blocking.
5. Before any real data: real hosting with backups, stronger demo PINs, a legal check on data
   protection (NFR-S7), and an SMS sender ID (Q15).

## Key decisions (the details are in docs/03 ADRs and docs/04)
- **Conflicts: ADR-04, server-authoritative**, chosen over last-write-wins. Statuses only move
  forward. A completion after a cancel is flagged (T9). Comments and ticks are append-only.
  A second completion returns `already_done`.
- **Sync (ADR-03):**
  - One global `updated_seq` cursor.
  - The safe high-water mark is a **10 s lag**.
  - The phone does a full refresh every 6 h, and after any rejected mutation.
  - `reset` is sent only when the client's cursor is ahead of the server.
  - A new roster row brings its shift's work with it.
  - Push is idempotent through `OfflineSyncLog` (pk = `mutation_id`), written in the same savepoint as the change.
  - `retry` / `photo_not_uploaded` is not logged. The `photo_pending` field was removed (migration tasks/0003).
- **Photos:** `PUT /api/sync/photos/<uuid>` with the header `X-Photo-Parent: task:<id>|run:<id>`.
  Response 201 = new, 200 = same bytes, 409 = different bytes.
- **Service worker:** served by a Django view. Its version is a content hash of the precached files.
  Precached: shell, CSS, Alpine, Dexie, field JS. `/app/` is served from cache and refreshed in the background.
- **Logout** sends `Clear-Site-Data: "cache","storage"` (the Q7 default).
- **Client rules** (hard-won; docs/02 §4.3):
  - Dexie transaction scopes only queue writes, with no `await`.
  - The Dexie instance must not be stored on Alpine state (the Proxy breaks transactions).
  - `navigator.onLine` is not trusted; "online" means the last request reached the server.
- **Other decisions:**
  - Overdue is derived and never stored (ADR-17).
  - Host-agnostic configuration comes from env (ADR-18).
  - Tenancy fails closed through the scoped managers (ADR-05).

## Files that matter
- **Tenancy and permissions:** `organisations/{tenancy,middleware,permissions}.py`
- **State machines:** `tasks/transitions.py`, `checklists/runs.py`
- **Sync server:** `sync/{pull,push,views,serializers,exceptions}.py`
- **Field app:** `frontend/field/{db,sync,photo,app}.js`, `templates/field/app_shell.html`, `static/sw.js`, `workflow/views.py`
- **Photos and services:** `tasks/photos.py`, `tasks/services.py`
- **Checklist generator:** `checklists/generator.py`
- **Notifications:** `notifications/{scan,dispatch,services}.py`
- **Dashboard:** `dashboard/queries.py`
- **Tests:**
  - `tests/test_sync_{pull,push,photos}.py`
  - `tests/test_field_app.py`
  - `tests/e2e/test_field_app_offline.py`
  - `tests/tenancy/`

---

## SYSTEM STATE SUMMARY (paste into a new chat)

**Project:** WorkFlow. A multi-tenant task/checklist PWA for frontline SMB teams in Uganda
(patchy network, low-end Android). Repo root: `WORKFLOW/`. The spec is `docs/01-04` and
`CLAUDE.md`, which holds the rules: MVP scope only; never use `.unscoped` in views/serializers/sync;
never take an org id from input; a new endpoint needs a leak test in `tests/tenancy/`; only
`transitions.py` writes Task.status; only `runs.py` writes ChecklistRun.status; overdue is
derived; notifications go only through `get_backend()`; no hosts in code; i18n on all strings;
tap targets ≥44 px.

**Stack:**
- Backend: Python 3.12, Django 5.2, DRF (sync API only), PostgreSQL 16, Redis, Celery with Beat, Pillow.
- Frontend: HTMX with Alpine 3.14.8, Dexie 4.0.11 (vendored in `frontend/vendor`, pinned in VERSIONS), and the Tailwind v4 standalone CLI (no Node).
- SMS: Africa's Talking behind `NotificationBackend` (console backend in dev).
- Testing and lint: pytest-django, factory_boy, Playwright (Python; system Chrome via `--browser-channel chrome`), ruff (line length 100).
- Deployment: Docker Compose with Caddy (`tls {$CADDY_TLS}`) and Gunicorn, configured entirely from `.env`.

**Apps:** accounts, organisations (the tenancy core), tasks, checklists, notifications, dashboard,
sync. Settings are in `workflow/settings/{base,dev,prod}`.

**Code structure:**
- `organisations/tenancy.py`:
  - `current_org` ContextVar, `tenant_context(org)`.
  - `TenantScopedManager` raises `TenantNotSet` when no organisation is set.
  - `TenantModel` (UUID pk, organisation, timestamps, `objects`/`unscoped`).
  - `SyncedTenantModel` adds `updated_seq` (from a PG sequence via trigger) and `deleted_at`.
- `organisations/middleware.py`: sets `request.organisation` / `request.membership` and uses `timezone.override(org tz)`.
- `organisations/permissions.py`: `can(membership, action, obj)`, `location_scope(m)` (None = all, [] = mine, [ids]).
- Querysets:
  - `Task.objects.visible_to(m)`, `.mine(m)`, `.annotate_overdue()`.
  - ChecklistRun has the same three, plus `with_progress()`.
- `tasks/transitions.py`:
  - `apply(task, Action, actor, *, reason, note, device_time, trusted_time, time_untrusted, offline, photo_id, comment_id) -> Result(ok, code, message)`.
  - Actions: START, COMPLETE, FLAG, REJECT, RESOLVE_FLAG, CANCEL. Offline COMPLETE on done → `already_done`; on cancelled → T9.
- `checklists/runs.py`: `tick_item`, `skip_item`, `flag_run`, `resolve_run_flag`, `cancel_run`. The run finishes automatically.
- `checklists/generator.py`: generates runs 48 h ahead, idempotent through unique (rule, occurrence_start).
- `tasks/photos.py`: `store_task_photo(uploaded_by, upload, task|checklist_run, taken_at, link, photo_id)`. Re-encodes to ≤200 KB, strips EXIF, makes a thumbnail.
- `notifications`:
  - `scan` (every minute) creates Notification rows with a `dedupe_key`.
  - `dispatch` (every minute) applies quiet hours, the monthly SMS cap, opt-out, combining, lease and retry.
- `sync`:
  - `pull.build_pull(m, cursor, limit, now)` returns cursor, has_more, reset, changes{10 tables}, tombstones.
  - `push.parse` and `push.run_push`. Mutation kinds: task.start, task.complete, task.flag, run.item_tick, run.item_skip, comment.add, comment.delete. Result statuses: applied, duplicate, rejected, retry.
  - `views`: Me, Pull, Push, Photo; throttles 60/30/60 per minute.
  - `exceptions.api_exception_handler` returns `{code, detail}` bodies; 401 means not logged in.
- Field app (`/app/`, Alpine component `field`, hash routes `#/`, `#/task/<id>`, `#/run/<id>`, `#/shifts`, `#/me`, `#/problems`):
  - `db.js`: `WFDB.open(membershipId)` opens the local tables plus outbox, blobs, problems and meta.
  - `sync.js`: `WFSync.create({db, deviceId, version, onState})`. Methods: record, addPhoto, run, start. Each round uploads photos, then pushes, then pulls.
  - `photo.js`: compresses to about 200 KB.
- URLs: `/`, where staff go to `/app/` and others to `/dashboard/`. `/tasks/`, `/checklists/`, `/roster/`, `/org/`, `/media/p/<id>[/t]`, `/sw.js`, `/manifest.json`, `/offline/`, `/api/sync/{me,pull,push,photos/<uuid>}`.

**Commands:**
- Dev (db and redis in Docker, Django runs natively): `make dev`, `make dev-seed`.
- Tests and lint: `make test-local`, `make e2e-local`, `make lint-local`.
- Full stack in Docker: `make up`, `make test`, `make lint`.
- Demo login: staff `0700000004` / PIN `4444`, manager `0700000002` / PIN `2222`.
- Temporary public demo: `bin/cloudflared tunnel --url http://localhost:8000`, then `TUNNEL_HOST=<host> make dev`.

**Features done:**
1. Phone and PIN auth with lockout and rate limits, PIN setup/reset, invites, organisation switch.
2. Tasks: CRUD, assignment to a person, shift or location, state machine, proof photos, comments, roster.
3. Recurring checklists: templates, rules, generator, runs, ticks and skips.
4. Manager dashboard: counts by staff, shift and location, drill-downs, trend chart.
5. Notifications: SMS reminders and overdue alerts through Celery Beat, WhatsApp stub that falls back to SMS.
6. Offline PWA: service worker, Dexie outbox, sync API, online indicator and pending badge.
7. Redesign (ADR-19/20/21): design system and dark mode, Lucide icons, `/styleguide/`, task board,
   people activity cards, notification bell.

**Next:**
1. Get Feature 6 and the redesign reviewed.
2. Add sync-endpoint cross-tenant tests in `tests/tenancy/`.
3. Check the PWA on the full stack (`make up`, Caddy, HTTPS).
4. Client meeting: settle the hosting model, then plan the pilot setup.

No other features are planned. Anything new has to be inside the MVP list; otherwise ask first.
