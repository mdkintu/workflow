// The staff field app (/app/): S4 My work, S5 task, S6 checklist run,
// shifts and "Me" (docs/04-design.md §3). Renders from the local store only,
// so it works the same offline; every action is written locally and queued
// for sync (sync.js). The server re-checks every rule (ADR-04).
//
// User-facing text comes from the page (`T`, translated by Django).
document.addEventListener("alpine:init", () => {
  "use strict";

  const T = JSON.parse(document.getElementById("wf-strings").textContent);
  const VERSION = document.documentElement.dataset.version || "";
  const HOUR = 60 * 60 * 1000;

  function storage(key, value) {
    try {
      if (value === undefined) return JSON.parse(localStorage.getItem(key));
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) { return null; }
    return value;
  }

  // Kept out of Alpine's reactive state on purpose: Alpine wraps plain
  // objects in Proxies, and Dexie must see its own objects to track
  // transactions.
  let db = null;
  let engine = null;

  Alpine.data("field", () => ({
    T: T,
    ready: false,
    needLogin: false,
    me: null,
    online: navigator.onLine,
    sync: { status: "synced", pending: 0, problems: 0, lastSyncAt: null, reachable: navigator.onLine },
    route: { name: "home", id: null },
    updateReady: false,

    // view data
    groups: [],
    showDone: false,
    task: null,
    run: null,
    items: [],
    comments: [],
    photos: [],
    shiftDays: [],
    problems: [],
    pendingIds: new Set(),

    // form state
    draftPhoto: null,     // {id, url} for the task being completed
    flagOpen: false,
    flagReason: "",
    flagNote: "",
    commentBody: "",
    skipping: null,
    skipReason: "",
    busy: false,
    error: "",

    async init() {
      window.addEventListener("online", () => { this.online = true; });
      window.addEventListener("offline", () => { this.online = false; });
      window.addEventListener("hashchange", () => this.go());
      // A new version took over from an older one (not the first install).
      if (navigator.serviceWorker && navigator.serviceWorker.controller) {
        navigator.serviceWorker.addEventListener("controllerchange", () => { this.updateReady = true; });
      }

      this.me = await this.loadMe();
      if (!this.me) { this.needLogin = true; this.ready = true; return; }

      const tz = this.me.organisation.timezone;
      this.dayFmt = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" });
      this.timeFmt = new Intl.DateTimeFormat(undefined, { timeZone: tz, hour: "2-digit", minute: "2-digit" });
      this.dateFmt = new Intl.DateTimeFormat(undefined, { timeZone: tz, weekday: "short", day: "numeric", month: "short" });

      db = WFDB.open(this.me.membership.id);
      let deviceId = await WFDB.getMeta(db, "deviceId", null);
      if (!deviceId) { deviceId = WFDB.uuid(); await WFDB.setMeta(db, "deviceId", deviceId); }
      engine = WFSync.create({
        db: db, deviceId: deviceId, version: VERSION,
        onState: (s) => {
          const finished = this.sync.status === "syncing" && s.status !== "syncing";
          this.sync = s;
          if (s.status === "login") this.needLogin = true;
          if (finished) this.render();
        },
      });
      if (this.me.server_time) engine.clock(this.me.server_time);
      await this.go();
      this.ready = true;
      engine.start();
    },

    /** /api/sync/me when online; the last copy when offline. */
    async loadMe() {
      const saved = storage("wf.me");
      try {
        const r = await fetch("/api/sync/me", { credentials: "same-origin", cache: "no-store" });
        if (r.status === 401 || r.status === 403) return null;
        if (r.ok) { const me = await r.json(); storage("wf.me", me); return me; }
      } catch (e) { /* offline */ }
      return saved;
    },

    // ---- routing --------------------------------------------------------

    async go() {
      const parts = (location.hash || "#/").slice(2).split("/");
      this.route = { name: parts[0] || "home", id: parts[1] || null };
      this.error = "";
      this.flagOpen = false;
      this.skipping = null;
      if (this.draftPhoto && this.route.name !== "task") this.discardDraftPhoto();
      await this.render();
      window.scrollTo(0, 0);
    },

    async render() {
      if (!db) return;
      this.pendingIds = await this.pendingSet();
      const r = this.route.name;
      if (r === "task") await this.loadTask(this.route.id);
      else if (r === "run") await this.loadRun(this.route.id);
      else if (r === "shifts") await this.loadShifts();
      else if (r === "problems") this.problems = await db.problems.orderBy("mutation_id").toArray();
      else await this.loadHome();
    },

    async pendingSet() {
      const ids = new Set();
      await db.outbox.each((m) => {
        ["task_id", "run_id", "run_item_id", "comment_id", "tick_id"].forEach((k) => {
          if (m.payload[k]) ids.add(m.payload[k]);
        });
      });
      return ids;
    },

    // ---- time helpers ---------------------------------------------------

    now() { return Date.now() + (this.sync.offsetMs || 0); },
    day(iso) { return iso && this.dayFmt ? this.dayFmt.format(new Date(iso)) : ""; },
    today() { return this.day(new Date(this.now()).toISOString()); },
    time(iso) { return iso && this.timeFmt ? this.timeFmt.format(new Date(iso)) : ""; },
    date(iso) { return iso && this.dateFmt ? this.dateFmt.format(new Date(iso)) : ""; },
    isOverdue(row) {
      return !["done", "cancelled"].includes(row.status) && Date.parse(row.due_at) < this.now();
    },
    syncedAt() { return this.sync.lastSyncAt ? this.time(this.sync.lastSyncAt) : ""; },

    // ---- S4 My work -----------------------------------------------------

    async rosterKeys() {
      const mine = await db.shift_assignments.filter((a) => a.membership_id === this.me.membership.id).toArray();
      const shifts = await db.shifts.bulkGet(mine.map((a) => a.shift_id));
      const onShift = new Set(), atLocation = new Set();
      mine.forEach((a, i) => {
        onShift.add(a.shift_id + "|" + a.date);
        if (shifts[i]) atLocation.add(shifts[i].location_id + "|" + a.date);
      });
      return { onShift: onShift, atLocation: atLocation };
    },

    /** The same "mine" as the server (Task/ChecklistRun .mine()). */
    isMine(row, kind, keys) {
      if (kind === "task") {
        const a = row.assignee;
        if (a.type === "membership") return a.id === this.me.membership.id;
        if (a.type === "shift") return keys.onShift.has(a.id + "|" + row.shift_date);
        return keys.atLocation.has(row.location_id + "|" + this.day(row.due_at));
      }
      if (row.shift_id) return keys.onShift.has(row.shift_id + "|" + row.shift_date);
      return keys.atLocation.has(row.location_id + "|" + this.day(row.occurrence_start));
    },

    async loadHome() {
      const keys = await this.rosterKeys();
      const locations = Object.fromEntries((await db.locations.toArray()).map((l) => [l.id, l.name]));
      const tasks = (await db.tasks.toArray()).filter((t) => this.isMine(t, "task", keys));
      const runs = (await db.runs.toArray()).filter((r) => this.isMine(r, "run", keys));
      const progress = await this.runProgress(runs.map((r) => r.id));
      const today = this.today();
      const tomorrow = this.dayFmt.format(new Date(this.now() + 24 * HOUR));
      const soon = this.now() + 2 * HOUR;
      const buckets = { overdue: [], now: [], later: [], tomorrow: [], done: [] };

      const rows = tasks.map((t) => ({ kind: "task", row: t, href: "#/task/" + t.id,
                                        sub: locations[t.location_id] || "" }))
        .concat(runs.map((r) => ({ kind: "run", row: r, href: "#/run/" + r.id,
                                    sub: (progress[r.id] || [0, 0]).join(" / ") + " " + T.done_lc })));
      rows.sort((a, b) => a.row.due_at.localeCompare(b.row.due_at));
      for (const item of rows) {
        const r = item.row;
        item.pending = this.pendingIds.has(r.id) || !!r._pending;
        if (r.status === "cancelled") continue;
        if (r.status === "done") {
          if (this.day(r.completed_at || r.due_at) === today) buckets.done.push(item);
        } else if (this.isOverdue(r)) buckets.overdue.push(item);
        else if (Date.parse(r.due_at) <= soon) buckets.now.push(item);
        else if (this.day(r.due_at) === today) buckets.later.push(item);
        else if (this.day(r.due_at) === tomorrow) buckets.tomorrow.push(item);
      }
      this.groups = [
        { key: "overdue", label: T.overdue, items: buckets.overdue },
        { key: "now", label: T.now, items: buckets.now },
        { key: "later", label: T.later_today, items: buckets.later },
        { key: "tomorrow", label: T.tomorrow, items: buckets.tomorrow },
        { key: "done", label: T.done_today, items: buckets.done },
      ];
    },

    async runProgress(runIds) {
      const items = await db.run_items.where("run_id").anyOf(runIds).toArray();
      const ticks = await db.run_item_ticks.where("run_item_id").anyOf(items.map((i) => i.id)).toArray();
      const resolved = new Set(ticks.map((t) => t.run_item_id));
      const out = {};
      items.forEach((i) => {
        out[i.run_id] = out[i.run_id] || [0, 0];
        out[i.run_id][1]++;
        if (resolved.has(i.id)) out[i.run_id][0]++;
      });
      return out;
    },

    // Which Lucide icon the row shows (the shell renders one per key).
    statusIcon(item) {
      const r = item.row;
      if (r.status === "done") return "done";
      if (r.status === "flagged") return "flagged";
      if (this.isOverdue(r)) return "overdue";
      if (r.status === "in_progress") return "progress";
      return item.kind === "run" ? "run" : "task";
    },

    statusLabel(row) {
      if (this.isOverdue(row)) return T.status.overdue;
      return T.status[row.status] || row.status;
    },

    // ---- S5 task --------------------------------------------------------

    async loadTask(id) {
      this.task = (await db.tasks.get(id)) || null;
      if (!this.task) return;
      const loc = await db.locations.get(this.task.location_id);
      this.task.locationName = loc ? loc.name : "";
      await this.loadComments({ task_id: id });
      this.photos = await this.photoRows("task_id", id);
    },

    can(action) {
      const t = this.task;
      if (!t) return false;
      if (action === "start") return t.status === "pending";
      if (action === "flag") return ["pending", "in_progress"].includes(t.status);
      if (action === "complete") {
        return ["pending", "in_progress"].includes(t.status) ||
          (t.status === "flagged" && t.flag && t.flag.kind === "problem");
      }
      return false;
    },

    async start() {
      const id = this.task.id;
      await engine.record("task.start", { task_id: id }, (db) => db.tasks.update(id, { status: "in_progress" }));
      await this.render();
    },

    async takePhoto(event, parentKind, parentId) {
      const file = event.target.files && event.target.files[0];
      event.target.value = "";
      if (!file) return null;
      this.busy = true;
      try {
        const blob = await WFPhoto.compress(file);
        const id = await engine.addPhoto(blob, parentKind, parentId);
        return { id: id, url: URL.createObjectURL(blob) };
      } catch (e) {
        this.error = T.photo_failed;
        return null;
      } finally {
        this.busy = false;
      }
    },

    async taskPhoto(event) {
      this.discardDraftPhoto();
      this.draftPhoto = await this.takePhoto(event, "task", this.task.id);
    },

    async discardDraftPhoto() {
      if (!this.draftPhoto) return;
      URL.revokeObjectURL(this.draftPhoto.url);
      await db.blobs.delete(this.draftPhoto.id);
      this.draftPhoto = null;
      await engine.refresh();
    },

    async complete() {
      const t = this.task;
      if (t.photo_required && !this.draftPhoto) { this.error = T.photo_needed; return; }
      const photo = this.draftPhoto;
      const payload = { task_id: t.id };
      if (photo) payload.photo_id = photo.id;
      await engine.record("task.complete", payload, (db) => {
        db.tasks.update(t.id, { status: "done", completed_by: this.me.membership.id,
                                completed_at: new Date(this.now()).toISOString() });
        if (photo) db.photos.put({ id: photo.id, task_id: t.id, run_id: null, _local: 1 });
      });
      this.draftPhoto = null;
      await this.render();
    },

    async flag() {
      if (!this.flagReason) { this.error = T.reason_needed; return; }
      const t = this.task;
      const commentId = WFDB.uuid();
      const reason = this.flagReason, note = this.flagNote.trim();
      await engine.record("task.flag", { task_id: t.id, reason: reason, note: note, comment_id: commentId },
        (db) => {
          db.tasks.update(t.id, { status: "flagged", flag: { kind: "problem", reason: reason } });
          db.comments.put(this.localComment(commentId, { task_id: t.id, is_flag: true,
                                                         body: note ? reason + ": " + note : reason }));
        });
      this.flagOpen = false; this.flagReason = ""; this.flagNote = "";
      await this.render();
    },

    // ---- S6 checklist run -----------------------------------------------

    async loadRun(id) {
      this.run = (await db.runs.get(id)) || null;
      if (!this.run) return;
      const items = await db.run_items.where("run_id").equals(id).sortBy("order");
      const ticks = await db.run_item_ticks.where("run_item_id").anyOf(items.map((i) => i.id)).toArray();
      const people = await this.peopleNames();
      this.items = items.map((item) => {
        const mine = ticks.filter((t) => t.run_item_id === item.id).sort((a, b) => (a.time || "").localeCompare(b.time || ""));
        const first = mine[0] || null;
        return Object.assign({}, item, {
          tick: first,
          by: first ? (people[first.membership_id] || "") : "",
          pending: first ? this.pendingIds.has(first.id) : false,
        });
      });
      this.run.done = this.items.filter((i) => i.tick).length;
      await this.loadComments({ run_id: id });
    },

    runOpen() {
      const r = this.run;
      return r && ["pending", "in_progress"].includes(r.status) &&
        (!r.available_from || Date.parse(r.available_from) <= this.now());
    },

    async resolveItem(item, skipped, reason, photoId) {
      const tickId = WFDB.uuid();
      const payload = { tick_id: tickId, run_item_id: item.id };
      if (skipped) payload.reason = reason;
      if (photoId) payload.photo_id = photoId;
      const runId = this.run.id;
      const remaining = this.items.filter((i) => !i.tick && i.id !== item.id);
      const anySkipped = skipped || this.items.some((i) => i.tick && i.tick.skipped);
      const status = remaining.length ? "in_progress" : (anySkipped ? "flagged" : "done");
      await engine.record(skipped ? "run.item_skip" : "run.item_tick", payload, (db) => {
        db.run_item_ticks.put({ id: tickId, run_item_id: item.id, membership_id: this.me.membership.id,
                                      skipped: skipped, skip_reason: reason || "", photo_id: photoId || null,
                                      time: new Date(this.now()).toISOString(), _local: 1 });
        db.runs.update(runId, { status: status });
      });
      this.skipping = null; this.skipReason = "";
      await this.render();
    },

    async tickWithPhoto(event, item) {
      const photo = await this.takePhoto(event, "run", this.run.id);
      if (photo) {
        URL.revokeObjectURL(photo.url);
        await this.resolveItem(item, false, "", photo.id);
      }
    },

    async skip(item) {
      if (!this.skipReason.trim()) { this.error = T.reason_needed; return; }
      await this.resolveItem(item, true, this.skipReason.trim());
    },

    async flagRun() {
      if (!this.flagReason) { this.error = T.reason_needed; return; }
      const commentId = WFDB.uuid();
      const body = this.flagNote.trim() ? this.flagReason + ": " + this.flagNote.trim() : this.flagReason;
      const runId = this.run.id;
      await engine.record("comment.add", { comment_id: commentId, run_id: runId, body: body, is_flag: true },
        (db) => {
          db.runs.update(runId, { status: "flagged" });
          db.comments.put(this.localComment(commentId, { run_id: runId, is_flag: true, body: body }));
        });
      this.flagOpen = false; this.flagReason = ""; this.flagNote = "";
      await this.render();
    },

    // ---- comments -------------------------------------------------------

    async peopleNames() {
      return Object.fromEntries((await db.people.toArray()).map((p) => [p.id, p.name]));
    },

    localComment(id, fields) {
      return Object.assign({ id: id, task_id: null, run_id: null, author_id: this.me.membership.id,
                             body: "", is_flag: false, photo_id: null,
                             time: new Date(this.now()).toISOString(), hidden: false, _local: 1 }, fields);
    },

    async loadComments(where) {
      const key = Object.keys(where)[0];
      const people = await this.peopleNames();
      const rows = await db.comments.where(key).equals(where[key]).toArray();
      rows.sort((a, b) => (a.time || "").localeCompare(b.time || ""));
      this.comments = rows.map((c) => Object.assign({}, c, {
        author: c.author_id === this.me.membership.id ? T.you : (people[c.author_id] || ""),
        pending: this.pendingIds.has(c.id),
        deletable: c.author_id === this.me.membership.id && !c.hidden &&
          this.now() - Date.parse(c.time) < 5 * 60 * 1000,
      }));
    },

    async addComment() {
      const body = this.commentBody.trim();
      if (!body) return;
      const id = WFDB.uuid();
      const parent = this.route.name === "task" ? { task_id: this.task.id } : { run_id: this.run.id };
      await engine.record("comment.add", Object.assign({ comment_id: id, body: body }, parent),
        (db) => db.comments.put(this.localComment(id, Object.assign({ body: body }, parent))));
      this.commentBody = "";
      await this.render();
    },

    async deleteComment(c) {
      await engine.record("comment.delete", { comment_id: c.id }, (db) => db.comments.delete(c.id));
      await this.render();
    },

    // ---- photos ---------------------------------------------------------

    async photoRows(key, id) {
      const rows = await db.photos.where(key).equals(id).toArray();
      const out = [];
      for (const p of rows) {
        const local = await db.blobs.get(p.id);
        out.push({ id: p.id, src: local ? URL.createObjectURL(local.blob) : p.thumb_url });
      }
      return out;
    },

    // ---- shifts, me, problems -------------------------------------------

    async loadShifts() {
      const mine = await db.shift_assignments.filter((a) => a.membership_id === this.me.membership.id).toArray();
      const shifts = Object.fromEntries((await db.shifts.toArray()).map((s) => [s.id, s]));
      const locations = Object.fromEntries((await db.locations.toArray()).map((l) => [l.id, l.name]));
      const today = this.today();
      this.shiftDays = mine.filter((a) => a.date >= today && shifts[a.shift_id])
        .sort((a, b) => a.date.localeCompare(b.date))
        .map((a) => {
          const s = shifts[a.shift_id];
          return { id: a.id, date: this.date(a.date + "T12:00:00Z"), name: s.name,
                   hours: s.start + "–" + s.end, location: locations[s.location_id] || "" };
        });
    },

    async dismissProblem(p) {
      await db.problems.delete(p.mutation_id);
      await engine.refresh();
      await this.render();
    },

    problemText(p) {
      return (T.codes[p.code] || p.message || p.code);
    },

    syncNow() { engine.run(); },

    logout(event) {
      if (this.sync.pending > 0 && !confirm(T.logout_pending.replace("%s", this.sync.pending))) {
        event.preventDefault();
      }
    },

    isOnline() { return this.online && this.sync.reachable !== false; },

    badge() {
      // `icon` is a key the shell maps to a Lucide icon (ADR-20).
      const s = this.sync;
      if (this.needLogin) return { cls: "is-danger", icon: "lock", text: T.log_in_again };
      if (s.problems > 0 || s.status === "error") return { cls: "is-danger", icon: "alert", text: T.sync_problem };
      if (s.pending > 0) return { cls: "is-warning", icon: "sync", text: T.waiting.replace("%s", s.pending) };
      if (!this.isOnline()) {
        // The dot next to it already says "Offline".
        return { cls: "is-offline", icon: "offline",
                 text: s.lastSyncAt ? T.last_sync + " " + this.syncedAt() : T.offline };
      }
      if (s.status === "syncing") return { cls: "is-offline", icon: "sync", text: T.syncing };
      return { cls: "", icon: "ok", text: T.synced + " " + this.syncedAt() };
    },
  }));
});
