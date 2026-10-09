# 03 — Architecture Decision Records: WorkFlow MVP

Status key: **Accepted** (in force), **Proposed** (needs an answer to an open question in
01-requirements §9), **Superseded**.
Each ADR is short on purpose. Add new ADRs at the end and never renumber them.

---

## ADR-01 — Server-rendered Django + HTMX monolith
**Status:** Accepted (the stack is fixed)
**Context:** A small team is building a narrow MVP. Managers' pages are forms, lists and a
dashboard. Pages must be light on 3G.
**Decision:** One Django 5 project with server-rendered templates. HTMX handles partial
updates (filters, inline status changes, dashboard refresh) and Alpine.js handles small
client-side state.
**Alternatives:**
- React/Vue SPA + REST/GraphQL API: two codebases, a heavier bundle, and state duplicated
  between client and server.
- Django admin only: not usable by the target users.
**Consequences:** fast delivery, little JS, and one place for validation and permissions.
Offline-capable screens need a different approach (ADR-02).

## ADR-02 — Hybrid offline: offline-first field app for staff, online HTMX for managers
**Status:** Accepted
**Context:** HTMX needs the server to render HTML, so it cannot work offline. Staff are the
ones who lose signal. Managers mostly work online and need up-to-date data.
**Decision:** The staff screens (My work, task detail, checklist run, my shifts, comments,
flags) are a small static app shell. Alpine components render it from Dexie (IndexedDB), and
a service worker caches it. All other screens are online-only HTMX pages with an offline
fallback page.
**Alternatives:**
- Make every screen offline, which means rebuilding the manager UI client-side (a big SPA).
- Cache HTMX responses in the service worker: stale, per-user HTML in caches, no offline writes.
- No offline support: fails the core requirement.
**Consequences:** Two rendering styles in one codebase. The field app must stay small
(≈5 screens), with a strict budget of ≤ 250 KB total (NFR-P1). The Python and JS sides share
status rules, which is covered by contract tests (ADR-16).

## ADR-03 — Custom outbox + cursor sync over DRF
**Status:** Accepted
**Context:** Offline changes need to reach the server reliably on flaky links. The data set per
user is small (tens to hundreds of records).
**Decision:**
- The phone keeps an **outbox** of typed mutations, each with a client-generated UUID, and
  pushes them to `POST /api/sync/push`.
- The server applies each mutation idempotently and returns a result per mutation.
- The phone then pulls changes with `GET /api/sync/pull?cursor=` using a global
  `updated_seq` sequence, with tombstones for deletions and visibility changes.
- Photos are uploaded separately and idempotently by UUID.
- To avoid missing rows committed out of order, the server serves only rows below a safe
  high-water mark. **Chosen at build (Feature 6): a time lag.** A pull stops just before the
  oldest row (above the client's cursor) written in the last `LAG = 10 s`; only a transaction
  held open for longer than that could be skipped, and every transaction in this app is short.
  Rejected: `pg_snapshot_xmin`, which is exact but needs a txid column on every synced table
  and a trigger to fill it, for a risk the lag already covers.
- **Self-healing:** every 6 hours the phone pulls from cursor 0 and drops local rows the server
  didn't send (except rows its outbox still refers to). This repairs anything a lag could
  miss, and hard-deleted roster rows. After any rejected mutation the next pull is a full one,
  so the phone's optimistic copy is replaced by the server's.
- **`reset: true`** is sent only when the client's cursor is ahead of the server's newest
  sequence (a restore from backup). A new roster row of the user's own instead brings that
  shift's tasks and runs with it in the same page, whatever their age. (A reset on roster
  changes was tried first: it restarted every first sync of anyone rostered today.)

**Alternatives:**
- PouchDB/CouchDB replication: adds a second database, the server logic is hard to express, and
  tenancy/permissions would move into CouchDB.
- Background Sync API only: not supported everywhere, and it only replays requests without
  reconciling state.
- A CRDT library (Automerge/Yjs): overkill for status changes and append-only comments, and adds
  weight.
- Timestamp-based `updated_at > since`: clock and ordering problems.

**Consequences:** We own the sync code (≈ a few hundred lines on each side) and must test it
well: duplicate pushes, partial failures, out-of-order commits, clock skew.

## ADR-04 — Server-authoritative conflicts with a restricted set of offline mutations
**Status:** Accepted
**Context:** Allowing general offline edits creates merge problems that users cannot sort out
on a small phone.
**Decision:** Offline clients can only send a closed list of mutations (start, complete, flag,
tick, skip, comment). Task definitions are edited only online. The server decides:
- Statuses merge forward only.
- Comments and ticks are append-only.
- Completions of cancelled tasks are kept and flagged.
- The first completion by trusted device time wins, and later ones become extra evidence.

Full rules: architecture §4.5.
**Alternatives:**
- Last-write-wins on whole records: silently loses work.
- A manual merge UI: too complex for users.
- Locking tasks while someone works on them: impossible offline.

**Consequences:** No data is ever silently thrown away. A few edge cases become "flagged for
review" instead of being resolved automatically.

## ADR-05 — Shared-schema multi-tenancy: organisation FK + fail-closed scoped manager
**Status:** Accepted
**Context:** Many small organisations share one install, and their data must be strictly
separated. There is one small machine and one operator.
**Decision:**
- Every tenant table has a non-null `organisation_id`.
- A `contextvars` holder records the current organisation for each request or Celery task.
- `TenantModel.objects` filters by it and **raises** when it is unset. `unscoped` is an
  explicit escape hatch for system code only.
- Foreign keys between records are checked to stay within one organisation.
- Every model and endpoint has a cross-tenant leak test.

**Alternatives:**
- **Schema per tenant** (django-tenants): stronger isolation, but migrations grow with the
  number of tenants, cross-tenant jobs get harder, and a `User` can't easily belong to two
  organisations.
- **Database per tenant:** heavy on one small machine, with more complicated backups.
- **PostgreSQL RLS** as the main mechanism: strong, but adds complexity to connection and
  session setup. **Kept as a possible future hardening layer.**
- Manual `filter(organisation=…)` in each view: relies on remembering to add it every time.
  One missed filter leaks data.

**Consequences:** Simple operations and cross-organisation reporting. Isolation depends on
application code, so the tests and the "no `unscoped` in request code" lint rule are required.

## ADR-06 — Phone number + PIN login, with a global User and a per-organisation Membership
**Status:** Proposed (PIN length and manager login depend on Q4)
**Context:** Staff may not have email addresses, find passwords hard, and share phones. SMS
costs money.
**Decision:**
- `User` is identified by an E.164 phone number and authenticates with a PIN hashed by
  Django's password hashers.
- `Membership(user, organisation, role)` holds the role.
- First-time setup and PIN resets use a one-time SMS code or link.
- Lockout after 5 failures (15 min), plus per-IP rate limiting in Redis.
- Sessions last 30 days.

**Alternatives:**
- Email + password: many staff have no email.
- An SMS OTP at every login: expensive and fails when SMS is slow.
- Magic links: need SMS and a browser handoff.
- A shared device PIN only: no accountability for who did what.

**Consequences:** A 4-digit PIN is weak on its own. The lockout, rate limiting and LAN-only
exposure make it acceptable for staff. Higher roles may need a longer PIN or a password (Q4).

## ADR-07 — UUID primary keys for tenant data
**Status:** Accepted
**Context:** Phones create comments, ticks and photos offline and must reference them before
the server has seen them.
**Decision:** UUIDv4 primary keys on all tenant models and on mutations. The client generates
IDs for anything it creates.
**Alternatives:**
- Integer IDs + temporary client IDs mapped after sync: complicated and error-prone.
- ULID/UUIDv7: better index locality, but no native Django 5 support. We may revisit if index
  bloat shows up at our scale (unlikely).

**Consequences:** Slightly larger indexes. IDs can't be guessed, which also helps file URLs.

## ADR-08 — Generate checklist runs ahead of time with a simple schedule model
**Status:** Accepted
**Context:** Offline phones must already hold the checklists they will need. Recurrence needs
are simple (daily, weekly, at shift start).
**Decision:**
- A `Schedule` model with `kind` (daily / weekly / shift_start), `times[]`, `weekdays[]`,
  `due_offset`, and active dates.
- A Beat job creates `ChecklistRun` rows up to 48 h ahead, protected by a unique
  `(schedule, occurrence_start)` constraint.

**Alternatives:**
- Compute runs when they are read: the phone would need the recurrence engine offline, and
  there would be nowhere to store progress.
- Full iCalendar RRULE (python-dateutil): too flexible for users, and adds a dependency.

**Consequences:** Rows exist for runs that may be cancelled later (cheap). Changing a schedule
only affects runs that haven't started yet.

## ADR-09 — Celery + Celery Beat for background and scheduled work
**Status:** Accepted (the stack is fixed)
**Context:** We need per-minute scans, retries with backoff for SMS, and daily jobs.
**Decision:**
- Celery with a Redis broker, two queues (`default`, `notifications`), and one Beat instance
  with the schedule in code.
- Tasks take IDs, are idempotent (backed by database constraints), use `acks_late`, and have
  time limits.

**Alternatives:**
- Host cron + management commands: no retries or queues, and hard to see inside.
- django-q2 / RQ / Huey: lighter, but the stack is fixed and Celery has well-known retry
  semantics.
- Postgres-backed queues (procrastinate): attractive, but outside the fixed stack.

**Consequences:** Two extra processes (worker, beat) to monitor. Redis is only a broker and
cache, so losing it loses no business data.

## ADR-10 — Notification provider interface; Africa's Talking first, console in dev
**Status:** Accepted (the WhatsApp provider is Proposed, Q3)
**Context:** SMS is how we reach staff who are offline in the app. Providers and prices change.
WhatsApp is popular but needs approved templates.
**Decision:**
- A `NotificationBackend` protocol (`send(OutboundMessage) -> SendResult`), chosen through
  settings for each channel.
- Implementations: `ConsoleBackend` (dev/test), `AfricasTalkingSMSBackend` (production) and a
  `WhatsAppBackend` stub.
- All sends are logged in `NotificationLog`, with de-duplication, quiet hours and a budget
  applied **before** the backend is called.

**Alternatives:**
- Calling the Africa's Talking SDK directly from domain code: locks us in and is hard to test.
- Twilio: higher cost and weaker local routes.
- An on-premise GSM modem: cheap per message but fragile hardware; possible as a future backend
  behind the same interface.

**Consequences:** Swapping or adding a provider is one class. Tests assert on console outbox
contents.

## ADR-11 — Photos compressed on the device, re-encoded on the server, stored on a local volume
**Status:** Accepted
**Context:** Photos are the largest payload, cost staff money, and may contain GPS metadata.
**Decision:**
- The phone resizes to ≤ 1280 px and JPEG q≈0.7 (~200 KB) to save data.
- The server re-encodes every upload to ≤ 200 KB regardless (it's the guarantee, not the phone).
- The server re-encodes with Pillow, strips all metadata, and makes a 240 px thumbnail.
- Files are stored at `media/org/<org>/photos/<uuid>.jpg` and served only through a permission
  check followed by a Caddy internal redirect.

**Alternatives:**
- Uploading originals (2–5 MB): unaffordable on mobile data and slow.
- S3/MinIO object storage: an extra service with no benefit on one machine.
- Storing images in PostgreSQL: bloats backups and the database.

**Consequences:** Photos are "good enough" as evidence, not archival quality. Media must be
included in backups.

## ADR-12 — Local HTTPS through Caddy's internal CA
**Status:** Proposed (Q12)
**Context:** Service workers need HTTPS. The server lives on a LAN without a public hostname.
**Decision:**
- Caddy `tls internal` for a fixed hostname (`workflow.lan`) resolved by the router's DNS.
- Phones install the root CA once through a guided `/setup` page (a QR code on a printed
  sheet).
- The CA is kept in a backed-up volume.

**Alternatives:**
- **A public domain + Let's Encrypt DNS-01** for a name that points to the LAN IP: no CA
  install, but needs a domain, a DNS API token and periodic internet access. **Preferred if the
  customer accepts buying a domain.**
- Plain HTTP: the PWA doesn't work.
- Self-signed certificates without a CA: browsers warn on every visit and the service worker
  won't register.

**Consequences:** Installing the certificate is extra onboarding work on every phone. If the CA
is lost, every device has to reinstall it. **ADR-18 makes the TLS mode an env setting**
(`CADDY_TLS=internal` or an ACME email), so this ADR describes the LAN default, not a fixed
choice.

## ADR-13 — Nightly pg_dump + media archive to an external disk
**Status:** Accepted (off-site copy Proposed, Q11)
**Context:** One machine, no DBA, RPO 24 h / RTO 4 h are acceptable.
**Decision:**
- A nightly `pg_dump -Fc` plus a zstd tar of media and the Caddy data, with checksums and a
  manifest.
- 7/4/6 rotation on an external disk.
- Scripted restore with a monthly drill into a throwaway Compose project, and backup status on
  the health page.

**Alternatives:**
- WAL archiving / PITR (pgBackRest): better RPO, but more moving parts than the operator can
  run.
- Streaming replication to a second machine: needs extra hardware.
- A cloud-managed database: goes against local hosting.

**Consequences:** Up to 24 h of server-side data can be lost (phones re-send what they still
hold in their outbox). The restore procedure must be practised.

## ADR-14 — Front-end build without Node: Tailwind standalone CLI + vendored libraries
**Status:** Accepted
**Context:** A small toolchain is easier to maintain. The runtime image must not include Node.
**Decision:**
- The Tailwind CSS standalone binary (pinned version) builds the CSS in a Docker stage and in
  `--watch` mode during development.
- HTMX, Alpine.js and Dexie.js are vendored as pinned, checksummed files in
  `frontend/vendor/`.
- The field app is plain ES modules, not bundled, and minified by a small Python step if
  needed.
- Playwright uses the Python package.

**Alternatives:**
- npm + Vite/esbuild: a bigger toolchain, an easy route to dependency growth, and Node in CI and
  dev.

**Consequences:** No tree-shaking. Libraries have to be updated by hand, with their version
recorded in `frontend/vendor/VERSIONS`. **Adding a JS dependency needs an ADR.**

## ADR-15 — Time handling: UTC in the database, organisation timezone in the UI, device and server times kept
**Status:** Accepted
**Context:** The dashboard's on-time and late judgements depend on times from phones whose
clocks may be wrong.
**Decision:**
- `USE_TZ=True`, and everything is stored in UTC.
- `Organisation.timezone` (default `Africa/Kampala`) is used for display, scheduling and quiet
  hours.
- Offline mutations store `device_time`, `received_at` and a derived "trusted time" (clock
  offset from the last sync, clamped to a plausible range). Details: architecture §4.6.

**Alternatives:**
- Trust device time: easy to game and often wrong.
- Trust server receipt time only: unfairly marks offline staff as late.

**Consequences:** A few more columns. Managers see a "synced late" label instead of a false
"overdue".

## ADR-16 — Testing strategy
**Status:** Accepted
**Decision:**
- **pytest-django + factory_boy** for models, services, views and APIs.
  - Factories always create data inside a given organisation.
  - `tests/tenancy/` holds the required cross-tenant leak tests (ADR-05).
- **Sync tests:** duplicate push, reordered push, partial failure, photo before and after its
  mutation, clock skew, completion of a cancelled task, and the cursor under concurrent writes.
- **Playwright (Python)** end-to-end tests on a mobile viewport with network throttling:
  - log in, complete a task with a photo, go offline (`context.set_offline(True)`), tick a
    checklist, reload the page while offline, go back online, and check that the dashboard
    reflects everything;
  - check the page-weight budget (NFR-P1) by summing transferred bytes.
- **Notification tests** assert on the console backend's outbox, and check that each SMS
  template fits one segment.
- `ruff check` + `ruff format --check` in CI. There is also a check that `.unscoped` does not
  appear in request code.

**Alternatives:** Selenium (slower, clunkier); Cypress (Node); unittest (more verbose than
pytest).
**Consequences:** E2E tests run in a separate, slower job (`pytest -m e2e`).

## ADR-17 — Overdue is derived, not stored
**Status:** Accepted (2026-09-28)
**Context:** The brief lists task states as `pending → in_progress → done / flagged / overdue`.
Overdue depends only on the time and the current status.
**Decision:**
- Stored `Task.status` values: `pending`, `in_progress`, `done`, `flagged`, `cancelled`.
- **Overdue is computed** as `due_at < now AND status IN (pending, in_progress)`: by a queryset
  annotation on the server, and in JS on the phone. It is shown in the UI as if it were a state.
- A supervisor's rejection becomes `flagged` (kind `rejected`). `verified` is deferred (Q6).
- Full machine: 04-design §5.

**Alternatives:**
- A Celery job that writes `status=overdue`. It bumps `updated_seq` on every late task, so each
  one is re-downloaded by every phone; offline phones would still show the task as not overdue;
  and it adds extra transitions (overdue → done/flagged).

**Consequences:** No background writes and fewer sync payloads. Phones show overdue correctly
while offline. Dashboard queries compare against `now()`, which the `(organisation, status,
due_at)` index covers.

## ADR-18 — Host-agnostic deployment through environment variables
**Status:** Accepted (2026-09-28)
**Context:** The MVP runs on a LAN server, but the product may later move to a public VPS. That
move must not need a code change or a special build.
**Decision:**
- Every host-specific value comes from **environment variables**: domain (`SITE_HOST`,
  `SITE_URL`), TLS mode (`CADDY_TLS`), `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`,
  `REDIS_URL`, media and static paths, SMS keys, ports, the image tag and feature switches that
  depend on reachability (`CA_SETUP_PAGE_ENABLED`, `SMS_DELIVERY_WEBHOOK_ENABLED`).
- One app image. No `.env` inside it.
- One Caddyfile that uses `{$SITE_HOST}` and `tls {$CADDY_TLS}`.
- `prod` settings refuse to start if a required variable is missing.
- Absolute URLs are built from `SITE_URL`. The front end uses only relative URLs.
- A guard test fails if hostnames or private IPs appear in code.
- The full contract and the LAN → VPS checklist are in architecture §9.1–9.2.

**Alternatives:**
- Separate images or builds per host: drift between them and a slower release.
- Host-specific settings modules (`settings/lan.py`, `settings/vps.py`): values end up in code.
- Separate Caddyfiles per host: acceptable, but unnecessary, because Caddy's `{$VAR}`
  substitution covers both modes.

**Consequences:** Moving from LAN to VPS means changing `.env`, restoring a backup, and phones
reinstalling from the new URL. Using a DNS-01 certificate on a LAN is the one case that needs a
different Caddy build and `tls` block. A few features (delivery webhooks, CA setup page) are
switched by env instead of being hard-wired to one topology.

---

## ADR-19 — Design system: tokens + component classes in Tailwind, Inter from Google Fonts, dark mode
**Status:** Accepted (2026-09-29). Product decision by the project owner; partly supersedes
NFR-P7 and NFR-S13 (fonts only).
**Context:** The UI was plain Tailwind utilities with no shared look. The owner asked for a
"SaaS dashboard" style (Linear/Vercel-like): one set of design tokens, reusable components, a
sidebar layout, and dark mode.
**Decision:**
- One stylesheet, still built by the Tailwind standalone CLI (ADR-14):
  `frontend/tailwind/input.css`. Tokens are CSS custom properties on `:root` (colours, radii,
  shadows). They are redefined under `prefers-color-scheme: dark` and under
  `[data-theme="dark"]`. They are exposed to Tailwind through `@theme inline`, so utilities
  like `bg-surface` and `text-muted` follow the theme. Components (`.btn`, `.card`, `.badge`,
  `.input`, `.nav-item`, `.kanban-col`, `.task-card`, `.avatar`, `.status-dot`, `.table`,
  `.modal`, …) live in `@layer components`.
- **Fonts:** Inter (400/500/600/700) and JetBrains Mono are loaded from **Google Fonts**, with
  system fallbacks. This is a deliberate exception to "no third-party CDNs": each page load
  sends the visitor's IP address and user agent to Google, and the field app shows system
  fonts when offline (the service worker does not cache cross-origin fonts). No scripts or
  analytics are loaded from third parties.
- **Dark mode:** follows the OS by default. A header toggle stores `light`/`dark` in
  `localStorage` (a per-device preference) and sets `data-theme` on `<html>`. The small
  `theme.js` script runs before first paint to avoid a flash of the wrong theme.
- **Sizes stay within §6.5:** body text and form fields ≥ 16 px, tap targets ≥ 44 px. Only
  small labels, badges and metadata use 12–14 px.
- `/styleguide/` renders every component in every state for visual QA (logged-in members).

**Alternatives:** self-hosting the fonts (no third-party request, works offline, but adds
~100 KB to the first load, which the owner declined); system fonts only (the previous default).
**Consequences:** Google sees page views from every install that has internet access. On a LAN
without internet the fonts silently fall back to system fonts. The CSS stays inside the field
app's 250 KB budget (checked by `tests/test_field_app.py`).

---

## ADR-20 — Lucide icons, vendored as SVG files and inlined by a template tag
**Status:** Accepted (2026-09-29)
**Context:** The UI used emoji and Unicode symbols as icons, which render differently on every
phone. The design system needs one consistent icon set (NFR-P7: inline SVG, no icon fonts).
**Decision:** Use **Lucide** (ISC licence). Only the icons we use are vendored, as SVG files in
`frontend/vendor/lucide/` and pinned in `frontend/vendor/VERSIONS`. `{% icon "name" %}`
(`workflow/templatetags/ui.py`) reads the file once, sets the size, `stroke-width="1.75"` and
`aria-hidden="true"`, and inlines it. There is no JS icon library, and icons work offline
because they are part of the HTML.
**Alternatives:** the Lucide JS package from a CDN (a runtime dependency that fails offline);
an SVG sprite (one more cached file, and `<use>` is awkward to style).
**Consequences:** Adding an icon means downloading its SVG from the pinned version into
`frontend/vendor/lucide/`. An unknown icon name raises an error when the template renders, so
typos fail in tests.

---

## ADR-21 — Task board, people activity cards and an in-app notification list
**Status:** Accepted (2026-09-29). Product decision by the project owner: these extend F1, the
People page and F5, and are added to the MVP list.
**Context:** The owner asked for a kanban board of tasks, "people cards" that show when each
person was last active, and a notification bell in the header. SMS stays the main channel.
**Decision:**
- **Task board (F1.6):** `/tasks/?view=board` shows the same filtered tasks as the list, in four
  columns (Pending, In progress, Flagged, Done). A card's "Next" button uses only the existing
  transitions in `tasks/transitions.py`: Start (pending), Mark done (in progress, when no photo is
  needed), Resolve (flagged). "Send back" on a done card opens the task, because rejecting needs
  a reason. There is no drag-and-drop and no backward move: statuses only move forward (ADR-04).
- **People activity (X.5):** a new unsynced tenant model `organisations.MemberActivity` (one row
  per membership) stores `last_seen_at`. The organisation middleware updates it at most once a
  minute per session. It is **not** on `Membership`, because every update to a synced row bumps
  `updated_seq`, and that would make every phone re-pull every membership every minute. Presence
  is derived: *online* if seen in the last 5 min, *away* if in the last hour, *offline* otherwise.
  The card's "status" line is the person's current in-progress task, or nothing. There is no
  location tracking (NFR-S5 still holds).
- **Notification list (F5.6):** the bell shows the signed-in member's own recent `Notification`
  rows (the SMS/WhatsApp messages already sent to them, last 7 days). A red dot means there is
  something newer than `MemberActivity.notifications_seen_at`. Opening the list marks it seen.
  It is a read-only list: no replies, no chat, no push notifications.

**Consequences:** One new model and migration. Two new routes (`/notifications/`,
`/notifications/seen/`) with cross-tenant tests. "Last active" is only as precise as one minute.
