# 01 — Requirements: WorkFlow MVP

Status: Draft for review · Last updated: 2026-09-28 · Source pitch: [WorkFlow.pdf](WorkFlow.pdf)

## 1. Purpose and scope

The pitch in `WorkFlow.pdf` describes a broad "virtual office": tasks, messaging, documents,
calendars and reminders for remote and on-site teams. **We are not building that.** We are
building a narrow MVP for one kind of customer:

> **Frontline and on-site teams in small and medium businesses** (for example hotels and guest
> houses, shops and supermarkets, security and cleaning firms, clinics and restaurants). They
> work in places with patchy internet, costly mobile data and low-end Android phones.

These teams mostly run their work through **WhatsApp groups and paper rosters or checklists**,
so those are what we have to beat. That means:

- It must be **faster than posting in a WhatsApp group** to assign a job and show it is done.
- It must **work with no signal** and not use up the staff member's data bundle.
- It must give the manager something WhatsApp and paper cannot: **a clear list of what is
  done, what is overdue and what has a problem**, with photo proof.

First market: **Uganda** (`+256` numbers, `Africa/Kampala` timezone, Data Protection and
Privacy Act 2019). The product stays sector-agnostic. In this document, examples from hotels,
retail and security only illustrate a point.

### 1.1 MVP features (the complete list)

| # | Feature | One-line description |
|---|---------|----------------------|
| F1 | Tasks & shift rosters | Assign work to a person, shift or location with a due time, status and proof photo. |
| F2 | Recurring checklists | Opening/closing, inspections and rounds are created automatically on a schedule. |
| F3 | Manager dashboard | Done / overdue / flagged, by staff, shift and location. |
| F4 | Task comments | Comments on a task or checklist run. No general chat. |
| F5 | Reminders & alerts | SMS/WhatsApp reminders before a due time and alerts when work is overdue. |

Added by product decision on 2026-09-29 (ADR-21): the task board (F1.6), people activity
cards (X.5) and the in-app notification list (F5.6). The look and feel is ADR-19/ADR-20.

Anything not on this list is out of scope (see §7).

---

## 2. Personas

### P1 — Owner ("Grace")
- Owns a business with 1–3 sites (for example a 30-room guest house and a small restaurant).
- Is often away from the site and checks in from her own phone, sometimes over mobile data.
- **Wants:** to know that the place is being run properly without phoning the manager.
- **Pain today:** she hears about problems late, from guests or customers, and has no record
  of who did what.
- **Uses:** the dashboard (read-only most days), user management, organisation settings.

### P2 — Manager ("David")
- Runs day-to-day operations at one or more locations and writes the weekly roster.
- Uses a mid-range Android phone, and sometimes a shared office PC.
- **Wants:** to assign work once, stop chasing people, and see what is late.
- **Pain today:** the WhatsApp group is noisy. "Done" messages have no proof, and paper
  checklists get lost or are filled in after the fact.
- **Uses:** rosters, task creation, checklist templates, the dashboard, reminder settings.

### P3 — Supervisor ("Aisha")
- Leads a shift on the floor (head housekeeper, shift supervisor, guard commander).
- Works on her feet with a low-end Android phone and moves between areas with poor signal.
- **Wants:** to see what her shift still has to do, check completion photos quickly, and send
  back work that is not done properly.
- **Uses:** the shift view, verify/reject, creating ad-hoc tasks for her shift, comments.

### P4 — Staff ("Peter")
- Housekeeper, cleaner, guard, shop assistant or cook.
- Has an entry-level Android phone (Android 8–12, 1–2 GB RAM, little free storage) or a shared
  device. Buys small data bundles and is often offline.
- May have limited reading confidence in English and uses WhatsApp comfortably.
- **Wants:** to know exactly what he has to do on this shift, prove he did it, and not get
  blamed for things he did.
- **Uses:** "My work" list, marking tasks done with a photo, ticking checklist items,
  comments, flagging a problem. He logs in with **phone number + PIN**.

---

## 3. Glossary

| Term | Meaning |
|------|---------|
| **Organisation** | One business (tenant). All data belongs to exactly one organisation. |
| **Location** | A place within an organisation where work happens (a site, building, floor or area). |
| **Membership** | A user's role in one organisation. One person can have memberships in several organisations. |
| **Shift** | A named time block at a location (for example "Morning 06:00–14:00, Main building"). |
| **Roster** | The assignment of staff to shifts on specific dates. |
| **Task** | One unit of work with an assignee (a person, a shift or a location), a due time and a status. |
| **Proof photo** | A photo attached when completing a task or checklist item. It can be required per task or item. |
| **Checklist template** | A reusable list of items (for example "Closing checklist — Bar") with a schedule. |
| **Checklist run** | One scheduled instance of a template (for example "Closing checklist — Bar — Tue 23:00"). |
| **Flag** | A problem raised on a task or run: staff report an issue, or a supervisor rejects a completion (see open question Q5). |
| **Overdue** | Past its due time and still `pending` or `in_progress`. This is derived, not a stored status (ADR-17). |

---

## 4. Roles and permissions

| Capability | Owner | Manager | Supervisor | Staff |
|------------|:-----:|:-------:|:----------:|:-----:|
| Edit organisation settings (name, timezone, SMS budget, quiet hours) | ✅ | — | — | — |
| Invite or remove users; change roles | ✅ | ✅ (not Owners) | — | — |
| Reset a staff member's PIN | ✅ | ✅ | ✅ (own shift staff) | — |
| Manage locations and shifts | ✅ | ✅ | — | — |
| Build and publish rosters | ✅ | ✅ | — | — |
| Create or edit checklist templates and schedules | ✅ | ✅ | — | — |
| Create or edit tasks | ✅ | ✅ | ✅ (own shift/location) | — |
| See tasks | All | All (their locations) | Own shift/location | Own + own shift's shared tasks |
| Complete tasks / tick checklist items | ✅ | ✅ | ✅ | ✅ (assigned to them or their shift) |
| Verify or reject a completion | ✅ | ✅ | ✅ | — |
| Raise a flag | ✅ | ✅ | ✅ | ✅ |
| Comment on a task or run | ✅ | ✅ | ✅ | ✅ (on tasks they can see) |
| View the dashboard | All locations | Their locations | Own shift/location | — |
| Configure reminders and alerts | ✅ | ✅ | — | — |

"Their locations" means the locations a Manager is linked to. A Manager with no linked
locations sees all locations.

---

## 5. User stories and acceptance criteria

The notation is **Given / When / Then**. "Offline" means the device has no connection to the
server. Each story ID is used for traceability in tests.

### F1 — Tasks & shift rosters

**F1.1 Create and assign a task** — *As a Manager, I want to assign a task to a person, a
shift or a location with a due time, so that it is clear whose job it is.*
- Given I am a Manager, when I create a task with a title, a location, a due date-time and an
  assignee that is **one of** a person, a shift or a location, then the task is saved with
  status `pending` and shows in each assignee's "My work" list on their next sync.
- Given the assignee is a shift, then every staff member rostered on that shift on the task's
  date sees it, and whoever completes it is recorded as the completer.
- Given the assignee is a location, then every staff member rostered at that location on that
  date sees it.
- The title is required (max 120 characters); the description is optional (max 1,000).
- The due time is entered and shown in the organisation's timezone and stored in UTC.
- I can mark a task "photo required". The default comes from organisation settings.
- A Supervisor can create tasks only for their own shift or location.

**F1.2 Task status lifecycle** — *As staff, I want to update my task's status so the manager
knows where it stands.*
- Stored statuses: `pending → in_progress → done`; `pending/in_progress → flagged` (problem
  reported); `done → flagged` (supervisor rejects); `flagged → in_progress` (flag resolved);
  any unfinished state → `cancelled` (Manager/Supervisor only). **Overdue** is derived from the
  due time, not stored. The full state machine is in `04-design.md` §5.
- Given a task is `photo required`, when I try to mark it `done` without a photo, then I am
  blocked with a clear message and a camera button.
- Given I mark a task `done`, then the completer, the device completion time and the server
  receipt time are all recorded.
- A transition that is not allowed is refused, both on the phone and on the server, with a
  reason.

**F1.3 Complete a task with a photo while offline** — *As staff, I want to finish my work and
attach a photo with no signal, and have it sync later.*
- Given I am offline and have opened the app at least once while online on this device, when
  I open "My work", then I see my tasks for today and the next day from local storage.
- When I take a photo and mark the task `done` offline, then the phone shows the task as done,
  with a visible "waiting to sync" marker.
- When the connection returns (or I reopen the app online), then the change and the photo
  upload automatically, the marker clears, and the manager sees the task as done.
- If I close the app or the phone restarts before syncing, the change and photo are still
  queued afterwards.
- The stored photo is ≤ 200 KB (the server re-encodes every upload); the phone also
  compresses before storing or uploading, to save data.

**F1.4 Verify or reject a completion** — *As a Supervisor, I want to check the photo and
accept or send back the work.*
- Given a task is `done`, I can leave it as it is (accepted) or reject it.
- When I reject it, then I must give a reason. The task becomes `flagged` (kind `rejected`), the
  reason is added as a comment, the completer is notified (F5), and it counts as flagged on the
  dashboard.
- A separate `verified` status is deferred until Q6 is answered.

**F1.5 Build a roster** — *As a Manager, I want to put staff on shifts for the week so tasks
assigned to a shift reach the right people.*
- I can define shifts per location (name, start time, end time, days of week). Shifts that
  cross midnight are supported.
- I can assign staff to a shift on specific dates, and copy last week's roster in one action.
- Given a staff member is on two overlapping shifts, then I see a warning but can still save.
- Changes to the roster reach staff phones on their next sync.
- Staff can see their own upcoming shifts (read-only) offline.

**F1.6 Task board** (ADR-21) — *As a Supervisor or Manager, I want to see the day's tasks as a
board so I can see at a glance what is waiting, in progress, flagged and done.*
- The tasks page has a List / Board switch. The board uses the same location and day filters.
- The board has four columns: Pending, In progress, Flagged, Done, each with a count.
- Each card shows the location, a status badge (overdue, flagged, done), the title, the
  details (two lines at most), the assignee and the due time.
- "Next" moves a card forward using only the transitions I am allowed (Start; Mark done when
  no photo is required; Resolve a flag). "Send back" on a done card opens the task to give a
  reason. Nothing can be dragged, and nothing moves backwards.

### F2 — Recurring checklists

**F2.1 Create a checklist template** — *As a Manager, I want to define a checklist once and
reuse it.*
- A template has a name, a location, an optional shift, and an ordered list of items
  (1–50). Each item has a label and flags for "photo required" and "must not be skipped".
- Editing a template affects only runs generated **after** the edit. Existing runs keep their
  items.

**F2.2 Schedule a checklist** — *As a Manager, I want checklists to appear automatically at
set times.*
- Schedules support: daily at one or more times; weekly on chosen days at chosen times; or
  "at the start of shift X". A due offset is also set (for example due 45 minutes after it
  becomes available).
- Given an active schedule, the system creates each run **up to 48 hours in advance**, so
  phones that go offline still have them.
- Runs are never duplicated, even if the scheduler runs twice or restarts.
- Pausing or ending a schedule stops future runs. Runs already created and not started are
  cancelled.

**F2.3 Complete a checklist run offline** — *As staff, I want to tick items as I go, even with
no signal.*
- Given a run is assigned to my shift or location, I can open it offline and tick items one at
  a time. Each tick stores who ticked it and when.
- Items marked "photo required" cannot be ticked without a photo.
- I can skip an item (unless it is "must not be skipped") only by giving a reason. A skipped
  item makes the run flagged.
- The run is `done` when every item is ticked or skipped. Partial progress is visible to the
  supervisor after sync.
- If two staff tick the same item offline, both ticks are kept and the earliest one counts.

### F3 — Manager dashboard

**F3.1 Status overview** — *As a Manager or Owner, I want to see done, overdue and flagged
work at a glance.*
- The dashboard shows counts of **done**, **overdue**, **flagged** and **open (not yet due)**
  for a chosen period (today by default; yesterday, last 7 days, or a custom range).
- It covers both tasks and checklist runs.
- The counts can be grouped by **staff**, **shift** or **location**, one grouping at a time.
- Tapping any count opens the matching list of tasks or runs.
- Supervisors see only their own shift or location. Managers see their locations. Owners see
  everything.

**F3.2 Performance on a slow connection**
- The dashboard's first view loads in ≤ 3 s on a simulated 3G connection once static assets
  are cached. The HTML response is ≤ 50 KB compressed for an organisation with 50 staff.
- The dashboard needs a connection. When offline it shows "You are offline — last updated
  HH:MM" and the last page it loaded, if one is cached.
- The dashboard refreshes itself at most once every 60 s while open, and also has a manual
  refresh button.

**F3.3 Late-synced work shown correctly**
- Given staff completed work offline before the due time but it synced after, then it counts
  as **done on time** and is labelled "synced late" (see the time rules in the architecture
  document).

### F4 — Comments on tasks

**F4.1 Comment on a task or run** — *As any user who can see a task, I want to add a comment
so that the discussion stays with the work.*
- Comments are plain text (max 500 characters) and may include one photo.
- Comments can be written offline and sync later in the order they were written.
- Comments are **append-only**: they cannot be edited. The author can delete a comment within
  5 minutes; after that, only a Manager can hide it, and a hidden comment still shows as a
  placeholder saying who hid it.
- New comments on my task appear the next time my phone syncs. There are no live chat
  features (typing indicators, read receipts, direct messages, group channels).

**F4.2 Flag a problem** — *As staff, I want to report that I cannot finish a task (for example
"no cleaning supplies") so that my manager knows.*
- Raising a flag needs a reason, chosen from a short per-organisation list or typed as free
  text, plus an optional photo.
- The flag appears as a comment with a flag marker, counts as flagged on the dashboard, and
  can alert the Supervisor (F5.3).
- A Supervisor or Manager can resolve the flag with a note.

### F5 — Reminders & overdue alerts (SMS / WhatsApp)

**F5.1 Reminder before due** — *As staff, I want a reminder before a task is due so I don't
forget, even if I haven't opened the app.*
- Each organisation sets a default reminder lead time (for example 30 minutes; 0 means off),
  and a task can override it.
- Reminders go to the person assigned. If a task is assigned to a shift or location, they go
  to the Supervisor on duty instead, to avoid a flood of messages.
- A reminder is sent **at most once** per task, and not if the task is already finished.

**F5.2 Overdue alert with escalation** — *As a Manager, I want to be told when work is late.*
- When a task or run becomes overdue, the assignee is alerted once. If it is still not done
  after the escalation delay (organisation setting, default 30 minutes), the Supervisor on
  duty is alerted. After a second delay, the Manager is alerted.
- Alerts for the same person that are due within 5 minutes of each other are combined into
  one message ("3 tasks overdue at Main building").

**F5.3 Flag alert** — when a flag is raised or a completion is rejected, the relevant
Supervisor (for a flag) or the completer (for a rejection) is notified.

**F5.4 Daily summary (optional per organisation)** — Managers and Owners can opt in to one
SMS at a set local time: "Yesterday: 42 done, 3 overdue, 2 flagged."

**F5.5 Delivery rules**
- Messages are ≤ 160 GSM-7 characters (one SMS segment) and include a short link to the task
  where possible.
- **Quiet hours** per organisation (default 22:00–06:00): messages are held until quiet hours
  end, except for staff on a shift during quiet hours.
- There is a monthly SMS cap per organisation. At 80% the Owner is warned. At 100% only
  escalations to Managers and Owners are sent.
- A user can turn off reminders for themselves. Managers cannot turn off overdue escalations
  sent to themselves.
- Every message sent is logged with its status (queued, sent, delivered, failed) and cost
  where the provider reports it.
- In development and tests, messages go to the console backend and are never really sent.

**F5.6 Notification list** (ADR-21) — *As any web user, I want to see the reminders and
alerts that were sent to me, in the app as well as by SMS.*
- A bell in the header lists my own notifications from the last 7 days, newest first
  (at most 20). A red dot shows when there is one I have not seen yet.
- Opening the list marks everything in it as seen. SMS/WhatsApp delivery is unchanged.
- I never see anyone else's notifications, including other organisations'.

### Cross-cutting: accounts and organisations

**X.1 Staff login with phone + PIN**
- Staff enter their phone number (Ugandan numbers accepted as `07XX…` or `+2567XX…` and stored
  in E.164 format) and a PIN.
- After 5 wrong PINs the account is locked for 15 minutes, and a Manager can unlock it. Login
  attempts are also rate-limited per IP address.
- The session lasts 30 days on a personal device, with an option to log out on shared
  devices (see Q7).
- Offline, a user who is already logged in stays logged in. A **new** login needs a
  connection.

**X.2 PIN setup and reset**
- Invited staff get an SMS with a one-time link or code to set their PIN on first login.
- A Manager or Supervisor can reset a PIN, which sends a new one-time code by SMS.

**X.3 Create an organisation and invite users** — An Owner (created by the system
administrator on this install) sets the organisation name, timezone, locations and SMS
settings, then invites users by phone number and role.

**X.4 One person, several organisations** — Given a phone number belongs to memberships in two
organisations, then after login the user picks one. Data never mixes between the two, and
they can switch without logging in again.

**X.5 People cards with activity** (ADR-21) — *As a Manager, I want to see who is active and
what they are working on.*
- The People page shows one card per person: name, role, locations, the organisation's
  timezone, "Active N min ago" and a presence dot (online ≤ 5 min, away ≤ 1 h, offline).
- If the person has a task in progress, the card quotes it ("Working on: …").
- "Active" means the person used WorkFlow (a page or a sync), recorded at most once a minute.
  No location is recorded.

---

## 6. Non-functional requirements

### 6.1 Performance on 3G and low-end devices
| ID | Requirement |
|----|-------------|
| NFR-P1 | First load of the staff app on a new device: ≤ 250 KB of JS + CSS + fonts (compressed), ≤ 400 KB total excluding photos. |
| NFR-P2 | Time to interactive ≤ 5 s on a simulated Slow 3G connection (≈400 kbps, 400 ms RTT) and a 4× CPU slowdown. |
| NFR-P3 | Repeat opens: the app shell is served from the service worker cache and "My work" renders from local data in ≤ 1 s, with or without a connection. |
| NFR-P4 | A sync pull for a typical staff member (≈30 tasks/runs changed) is ≤ 30 KB compressed. Syncs send only what changed. |
| NFR-P5 | Stored photos are ≤ 1280 px on the long side and ≤ 200 KB, re-encoded on the server; the phone compresses to the same target before upload. Thumbnails are ≤ 20 KB. |
| NFR-P6 | Server p95 ≤ 300 ms for page and API requests, at the scale stated in Q13, on the target host machine. |
| NFR-P7 | No icon fonts (inline SVG icons, Lucide per ADR-20), no third-party scripts or analytics. Web fonts: Inter and JetBrains Mono from Google Fonts, with system fallbacks (ADR-19). |
| NFR-P8 | Supported: Chrome for Android on Android 8+ with ≥ 1 GB RAM, plus current desktop Chrome, Edge and Firefox for managers. |

### 6.2 Offline rules
| ID | Rule |
|----|------|
| NFR-O1 | **Works offline:** staff "My work", task detail, checklist run, own upcoming shifts, adding comments and flags, taking photos. |
| NFR-O2 | **Can be changed offline:** task status changes, checklist ticks and skips, comments, flags, photos. |
| NFR-O3 | **Online only:** creating or editing tasks, templates, schedules, rosters, users and settings; the dashboard; reject/resolve flag; login. |
| NFR-O4 | Local data covers a window of 3 days back and 2 days ahead. Older data is removed from the phone automatically once it has synced. |
| NFR-O5 | Nothing entered offline may be lost when the app closes, the browser is killed or the phone restarts. Changes are written to IndexedDB before the UI confirms them. |
| NFR-O6 | The app always shows its sync state (synced, waiting with a count, syncing, error) and the time of the last successful sync. |
| NFR-O7 | Each offline change is sent exactly once in effect: retries are safe and never create duplicates. |
| NFR-O8 | If local storage is nearly full, the user is warned and photos already synced are removed from the phone first. |

### 6.3 Security and data protection
*This section reflects our reading of the Uganda Data Protection and Privacy Act 2019 and the
Data Protection and Privacy Regulations 2021. It is **not legal advice**; confirm with counsel
before launch.*

| ID | Requirement |
|----|-------------|
| NFR-S1 | **Tenant isolation:** no user can read or change another organisation's data through any page, API, file URL or export. Automated tests cover this for every model and endpoint. |
| NFR-S2 | PINs and passwords are stored only as salted hashes (Django's password hashers). PINs are never logged or shown again after they are set. |
| NFR-S3 | Login throttling and lockout as in X.1. Sessions are HTTP-only, Secure and SameSite=Lax cookies. CSRF protection is on for all forms and the sync API. |
| NFR-S4 | All traffic uses HTTPS, including on the local network (see architecture §7). |
| NFR-S5 | **Data minimisation:** we collect only name, phone number, role and work records. We collect no national ID, date of birth or location tracking. EXIF metadata (including GPS) is stripped from every photo. |
| NFR-S6 | **Legal basis and notice:** staff see a short privacy notice at first login explaining what is collected and why. The organisation (employer) is the data controller; the operator of the install is a data processor. |
| NFR-S7 | **Registration:** the operator and each customer organisation may need to register with the Personal Data Protection Office (PDPO). This is included in the launch checklist. |
| NFR-S8 | **Data-subject rights:** an Owner can export all data about one user (JSON/CSV) and can delete or anonymise a user. Work records stay but are attributed to "Former staff #N". |
| NFR-S9 | **Retention:** task, comment and notification records are kept for 24 months by default. Photos are kept for 90 days by default (Q8). Both are configurable per organisation. A daily job purges expired data. |
| NFR-S10 | **Breach handling:** there is a documented procedure to notify the PDPO and affected organisations without delay. Logs contain enough detail to tell which organisation and which data were affected. |
| NFR-S11 | **Audit log:** changes to users, roles, PINs, tasks (status/assignee/due), templates and settings record who, what and when. Owners can see the audit log. |
| NFR-S12 | Photos are served only to logged-in users of the owning organisation. File URLs are not guessable and are checked on every request. |
| NFR-S13 | Data stays on the host we deploy to (the LAN server for the MVP). The only outbound calls are to the SMS/WhatsApp provider (phone number + message text), if enabled the off-site backup target (Q11), and — from users' browsers — Google Fonts (ADR-19). |

### 6.4 Reliability, backups and operations
| ID | Requirement |
|----|-------------|
| NFR-R1 | Nightly automatic backup of the database, uploaded media and TLS CA data. |
| NFR-R2 | Retention: 7 daily, 4 weekly and 6 monthly copies, on a disk separate from the one the database runs on. |
| NFR-R3 | RPO ≤ 24 h (maximum data loss); RTO ≤ 4 h (time to restore on replacement hardware). |
| NFR-R4 | Restores are tested with a documented monthly drill. The Owner or admin can see the date of the last successful backup. |
| NFR-R5 | All services restart automatically after a power cut. The stack starts cleanly on boot without anyone stepping in. |
| NFR-R6 | If internet access to the SMS provider is lost, messages queue and retry for up to 24 h, after which they are marked failed and shown on the dashboard. |
| NFR-R7 | A health page shows the database, Redis, workers, the scheduler, disk space and the last backup. |
| NFR-R8 | **Host-agnostic:** every host-specific value (domain, TLS mode, allowed hosts, database/Redis URLs, media paths, SMS keys, ports) comes from environment variables. The same images run on a LAN server and a public VPS with only `.env` changes (and the Caddyfile only if DNS-01 is used). No such values appear in code, templates or images (ADR-18). |

### 6.5 Usability and localisation
- English at launch. All UI strings are wrapped for translation (Django i18n) so that Luganda
  or Swahili can be added later without code changes (Q10).
- Icons as well as text on primary actions; tap targets ≥ 44 × 44 px; base font ≥ 16 px;
  good contrast (WCAG AA).
- Staff can get from app open to marking a task done with a photo in ≤ 3 taps plus the camera.
- Times and dates use the organisation's timezone and the format `Tue 14 Oct, 14:30`.
- Phone numbers are shown in local format (`0772 123 456`) and stored as E.164.

---

## 7. Out of scope for the MVP

These items are from the pitch or are commonly requested, and they are **deliberately
excluded**. Adding any of them needs an explicit product decision and an ADR.

- **Full calendar** (shared calendars, meetings, milestones, calendar views). Rosters and due
  times are all the time management we need.
- **Document storage and sharing** (file libraries, attachments other than proof and comment
  photos, versioning).
- **Video**: recording, upload, calls or meetings.
- **General messaging**: group chats, direct messages, channels, broadcast announcements,
  read receipts, typing indicators. Only comments on a task or run are allowed.
- **Third-party integrations**: payroll, HR, hotel PMS, POS, accounting, Google/Microsoft
  calendars, Slack/Teams, webhooks, public API.
- Time and attendance / clock-in-out, GPS tracking and geofencing.
- Inventory, stock or purchasing.
- Native Android/iOS apps (the PWA only).
- Running on a public VPS or cloud, or as SaaS. The MVP is deployed on one LAN machine. The app
  must still **stay deployable to a public VPS through configuration only** (NFR-R8), but doing
  that deployment is not an MVP deliverable.
- Self-service sign-up and billing or payments.
- Advanced analytics, charts beyond the dashboard counts, scheduled reports.
- Multi-language UI at launch (prepared, not delivered).
- Customer or guest-facing features.

---

## 8. Pilot success metrics

| Metric | Target after 8 weeks at a pilot organisation |
|--------|--------------------------------------------|
| Weekly active staff / rostered staff | ≥ 80% |
| Tasks and runs completed through the app (not paper) | ≥ 90% |
| Completions with a photo where a photo is required | ≥ 95% |
| Overdue rate | Falls by ≥ 30% from week 2 to week 8 |
| Manager-reported reduction in work-related WhatsApp messages | Qualitative: "noticeably fewer" |
| Changes made offline that were lost | 0 |
| Average SMS per staff member per week | ≤ 5 (cost control) |

---

## 9. Open questions

These need a product answer before or during build. Where a default is assumed elsewhere in
these documents, it is marked.

| # | Question | Why it matters | Assumed default until answered |
|---|----------|----------------|--------------------------------|
| Q1 | **Hosting model.** "Many businesses on one install" and "reached over the local network" pull against each other. Is it one machine per business site (multi-tenant as a safeguard), or one shared machine that several businesses reach? If shared, how do other sites reach it: VPN, tunnel or public exposure? | Sets the network design, TLS, the threat model and the backup responsibility. | One machine on one site's LAN, running one or more organisations that all reach it over that LAN. |
| Q2 | Do staff or owners ever need to sync from outside the business Wi-Fi (for example over mobile data at home)? | If yes, the server must be reachable from the internet (tunnel/VPN), which changes the security posture. | No. Phones sync when they are back on the site Wi-Fi. |
| Q3 | WhatsApp: through Africa's Talking, the Meta WhatsApp Cloud API, or deferred? Is SMS alone acceptable for the MVP? Who pays for messages? | WhatsApp needs a business account, approved templates and an internet uplink, and pricing differs. | SMS only in the MVP. The WhatsApp backend is a stub behind the same interface. |
| Q4 | How do Owners and Managers log in: phone + PIN like staff, or phone + password (optionally with an SMS code)? What PIN length? | Security of accounts that can see all data. | Staff: 4-digit PIN. Supervisor and above: 6-digit PIN. |
| Q5 | Exactly what counts as "flagged": a problem raised by staff, a completion rejected by a supervisor, a skipped checklist item, or all three? | Defines the dashboard counts. | All three. |
| Q6 | Is supervisor verification required for every completion, optional per task, or not in the MVP? | Adds a status and a workflow step. | Optional per task or template, off by default. |
| Q7 | Do several staff share one phone? | Affects offline data scoping, fast user switching and logout. | Mostly personal phones. "Log out" clears local data. |
| Q8 | How long must photos be kept? Can photos show guests or customers, and should we give staff guidance? | Data protection and disk use. | 90 days. The privacy notice tells staff not to photograph people. |
| Q9 | Is a CSV export of dashboard or task data needed? | Small feature, but not on the MVP list. | Out of scope. |
| Q10 | Which UI languages at launch? | Translation effort and layout. | English only, prepared for translation. |
| Q11 | Off-site backup: is an encrypted copy to cloud storage acceptable, or must all data stay on the premises? | Resilience against fire or theft vs data residency. | Local external disk only. Off-site is optional and off by default. |
| Q12 | Local certificates (LAN mode only; a VPS uses a public certificate automatically, ADR-18): is installing a root certificate on every phone acceptable, or will we buy a domain and use a public certificate (DNS-01) that points to the LAN IP? | Onboarding friction for every staff device. | Caddy internal CA plus a guided install page. |
| Q13 | Expected scale per install: number of organisations, staff per organisation, tasks and runs per day? | Hardware sizing and performance targets. | ≤ 20 organisations, ≤ 100 staff each, ≤ 5,000 tasks/runs per day in total. |
| Q14 | Who runs the machine (the business, us, or a local IT partner), and who handles backups, updates and power (UPS)? | Operations runbook and support model. | Us or a partner, with a written runbook. |
| Q15 | SMS sender ID: will we register an alphanumeric sender ID for Uganda, or use a shared short code? | Delivery rates and lead time with the provider. | Register a sender ID through Africa's Talking. |
