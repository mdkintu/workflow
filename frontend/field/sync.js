// Sync engine for the field app (docs/02-architecture.md §4.4, docs/04-design.md §4.2-4.6).
//
// One round: upload photos → push the outbox → pull until has_more is false.
// Push comes before pull so the server has the phone's changes before the
// phone downloads the new state. Rounds never overlap; asking for one while
// one is running queues exactly one more.
//
// Triggers: app open, the `online` event, every 60 s while the page is
// visible (15 s while the server is unreachable), shortly after every
// change made on the phone, and "Sync now". navigator.onLine isn't trusted:
// a round just tries, and a failed request costs no data.
// Every 6 hours the pull starts from 0 and rows the server no longer sends
// are dropped (heals roster removals and anything missed).
(function () {
  "use strict";

  const PUSH_BATCH = 50;
  const FULL_REFRESH_MS = 6 * 60 * 60 * 1000;
  const INTERVAL_MS = 60 * 1000;
  const RETRY_OFFLINE_MS = 15 * 1000;  // unreachable: a failed request costs no data
  const WINDOW_BACK_MS = 3 * 24 * 60 * 60 * 1000;
  const CHILD_TABLES = ["run_items", "run_item_ticks", "comments", "photos"];
  // Warnings on an applied change that the person should see (docs/04 §4.5).
  const WARNINGS = ["task_cancelled_flagged", "already_done"];

  class AuthError extends Error {}
  class HttpError extends Error {
    constructor(status, code) { super(code || String(status)); this.status = status; this.code = code; }
  }

  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function create(opts) {
    const db = opts.db;
    const onState = opts.onState || function () {};
    let running = null;
    let again = false;
    let backoffUntil = 0;
    let seqCounter = 0;
    let timer = null;
    // `reachable`: did the last request get an answer? navigator.onLine is
    // true on Wi-Fi with no internet, so it only tells us when we're surely off.
    const state = { status: navigator.onLine ? "synced" : "offline", pending: 0, problems: 0,
                    lastSyncAt: null, offsetMs: 0, reachable: navigator.onLine };

    async function emit(patch) {
      Object.assign(state, patch || {});
      state.pending = await db.outbox.count() + await db.blobs.where("uploaded").equals(0).count();
      state.problems = await db.problems.count();
      onState(Object.assign({}, state));
    }

    async function api(method, url, body, headers) {
      const h = Object.assign({ "X-Device-Id": opts.deviceId, "X-Client-Version": opts.version,
                                "X-CSRFToken": csrfToken() }, headers || {});
      if (body !== undefined && !(body instanceof Blob)) {
        h["Content-Type"] = "application/json";
        body = JSON.stringify(body);
      }
      let response;
      try {
        response = await fetch(url, { method: method, body: body, headers: h,
                                      credentials: "same-origin", cache: "no-store" });
      } catch (e) {
        state.reachable = false;
        throw e;
      }
      state.reachable = true;
      if (response.status === 401) throw new AuthError();
      if (response.status === 429 || response.status >= 500) {
        const wait = parseInt(response.headers.get("Retry-After") || "30", 10);
        backoffUntil = Date.now() + wait * 1000;
        throw new HttpError(response.status);
      }
      return response;
    }

    async function readJson(response) {
      try { return await response.json(); } catch (e) { return {}; }
    }

    // ---- local changes ------------------------------------------------

    /** Records a change: `apply(db)` updates the read models and the outbox
     * row is written in the same Dexie transaction (NFR-O5).
     *
     * Rule for every transaction here: the scope only *queues* writes, with
     * no `await` (reads are done before). IndexedDB runs them in order and
     * commits when all are done; any failure aborts all of them. Dexie can't
     * reliably follow native `await` inside a transaction in current Chrome
     * (PrematureCommitError), so we don't rely on it. */
    async function record(kind, payload, apply) {
      const mutation = {
        mutation_id: WFDB.uuid(), kind: kind, payload: payload,
        device_time: new Date(Date.now() + state.offsetMs).toISOString(),
        seq: Date.now() * 1000 + (seqCounter++ % 1000), attempts: 0,
      };
      const tables = [db.outbox].concat(WFDB.READ_TABLES.map((t) => db[t]));
      await db.transaction("rw", tables, () => {
        if (apply) apply(db);
        db.outbox.add(mutation);
      });
      await emit();
      soon();
      return mutation;
    }

    async function addPhoto(blob, parentKind, parentId) {
      const id = WFDB.uuid();
      await db.blobs.add({ id: id, blob: blob, parentKind: parentKind, parentId: parentId,
                           takenAt: new Date(Date.now() + state.offsetMs).toISOString(),
                           uploaded: 0 });
      await emit();
      return id;
    }

    // ---- the round ----------------------------------------------------

    async function uploadPhotos() {
      const waiting = await db.blobs.where("uploaded").equals(0).toArray();
      for (const p of waiting) {
        const response = await api("PUT", "/api/sync/photos/" + p.id, p.blob, {
          "Content-Type": "image/jpeg", "X-Photo-Parent": p.parentKind + ":" + p.parentId,
          "X-Taken-At": p.takenAt,
        });
        if (response.status === 201 || response.status === 200) {
          await db.blobs.update(p.id, { uploaded: 1 });
        } else if (response.status === 409) {
          await renamePhoto(p);  // same id, different bytes: re-link under a new id
        } else {
          // 404 (the task is gone), 413/415: this photo can't be sent. The
          // change that uses it is rejected by the server and shown then.
          await db.blobs.update(p.id, { uploaded: 1, failed: response.status });
        }
      }
    }

    async function renamePhoto(p) {
      const newId = WFDB.uuid();
      const users = (await db.outbox.toArray()).filter((m) => m.payload.photo_id === p.id);
      await db.transaction("rw", db.blobs, db.outbox, () => {
        db.blobs.delete(p.id);
        db.blobs.add(Object.assign({}, p, { id: newId }));
        users.forEach((m) => db.outbox.put(Object.assign({}, m, {
          payload: Object.assign({}, m.payload, { photo_id: newId }) })));
      });
    }

    async function push() {
      for (;;) {
        const ready = await db.outbox.orderBy("seq").limit(PUSH_BATCH).toArray();
        if (!ready.length) return;
        const response = await api("POST", "/api/sync/push", {
          device_id: opts.deviceId,
          mutations: ready.map((m) => ({ mutation_id: m.mutation_id, kind: m.kind,
                                         device_time: m.device_time, payload: m.payload })),
        });
        const data = await readJson(response);
        if (!response.ok) throw new HttpError(response.status, data.code);
        clock(data.server_time);
        let progressed = false;
        for (const r of data.results) {
          const m = ready.find((x) => x.mutation_id === r.mutation_id);
          if (r.status === "retry") {
            await db.outbox.update(r.mutation_id, { attempts: (m.attempts || 0) + 1 });
            continue;
          }
          progressed = true;
          await db.outbox.delete(r.mutation_id);
          // The phone guessed wrong: the next pull starts from 0 and replaces
          // its optimistic copy with the server's.
          if (r.status === "rejected") await WFDB.setMeta(db, "lastFullAt", 0);
          if (r.status === "rejected" || WARNINGS.includes(r.code)) {
            await db.problems.put({ mutation_id: r.mutation_id, kind: m.kind, payload: m.payload,
                                    status: r.status, code: r.code, message: r.message || "",
                                    at: new Date().toISOString() });
          }
        }
        if (!progressed) return;  // only retries left: try again next round
      }
    }

    function clock(serverTime) {
      if (serverTime) state.offsetMs = Date.parse(serverTime) - Date.now();
    }

    async function pull() {
      const lastFull = await WFDB.getMeta(db, "lastFullAt", 0);
      const full = Date.now() - lastFull > FULL_REFRESH_MS;
      let cursor = full ? 0 : await WFDB.getMeta(db, "cursor", 0);
      const seen = {};
      for (;;) {
        const response = await api("GET", "/api/sync/pull?cursor=" + cursor);
        const data = await readJson(response);
        if (!response.ok) throw new HttpError(response.status, data.code);
        clock(data.server_time);
        if (data.reset) {
          await db.transaction("rw", WFDB.READ_TABLES.map((t) => db[t]).concat([db.meta]),
            () => {
              WFDB.READ_TABLES.forEach((t) => db[t].clear());
              WFDB.setMeta(db, "cursor", 0);
            });
          if (cursor === 0) return;  // shouldn't happen; don't loop
          cursor = 0;
          continue;
        }
        await applyPull(data, seen);
        cursor = data.cursor;
        await WFDB.setMeta(db, "cursor", cursor);
        if (!data.has_more) break;
      }
      if (full) {
        await dropUnseen(seen);
        await WFDB.setMeta(db, "lastFullAt", Date.now());
      }
      await pruneWindow();
    }

    /** Ids the outbox still refers to: their local rows stay as they are. */
    async function pendingTargets() {
      const ids = new Set();
      await db.outbox.each((m) => {
        ["task_id", "run_id", "comment_id", "tick_id", "photo_id"].forEach((k) => {
          if (m.payload[k]) ids.add(m.payload[k]);
        });
      });
      return ids;
    }

    async function applyPull(data, seen) {
      const keep = await pendingTargets();
      const rowsByTable = {};
      for (const table of WFDB.READ_TABLES) {
        let rows = data.changes[table] || [];
        seen[table] = seen[table] || new Set();
        rows.forEach((r) => seen[table].add(r.id));
        const kept = rows.filter((r) => keep.has(r.id)).map((r) => r.id);
        if (kept.length && (table === "tasks" || table === "runs")) {
          // A change still waiting to go keeps its local status.
          const local = Object.fromEntries((await db[table].bulkGet(kept)).filter(Boolean).map((r) => [r.id, r]));
          rows = rows.map((r) => local[r.id] ? Object.assign({}, r, { status: local[r.id].status, _pending: 1 }) : r);
        }
        rowsByTable[table] = rows;
      }
      // Reads first, then only writes inside the transaction: Dexie loses
      // a transaction whose reads interleave with the screen's own reads.
      const drop = emptyDrop();
      for (const t of data.tombstones || []) {
        if (drop[t.type]) drop[t.type].push(t.id);
      }
      await addChildren(drop, drop.tasks, drop.runs);
      const tables = WFDB.READ_TABLES.map((t) => db[t]);
      await db.transaction("rw", tables, () => {
        for (const table of WFDB.READ_TABLES) {
          if (rowsByTable[table].length) db[table].bulkPut(rowsByTable[table]);
        }
        deleteKeys(drop);
      });
    }

    function emptyDrop() {
      return Object.fromEntries(WFDB.READ_TABLES.map((t) => [t, []]));
    }

    /** Adds the children of these tasks and runs to `drop`. */
    async function addChildren(drop, taskIds, runIds) {
      if (!taskIds.length && !runIds.length) return;
      const items = await db.run_items.where("run_id").anyOf(runIds).primaryKeys();
      drop.run_items.push(...items);
      drop.run_item_ticks.push(...await db.run_item_ticks.where("run_item_id").anyOf(items).primaryKeys());
      for (const [key, ids] of [["task_id", taskIds], ["run_id", runIds]]) {
        drop.comments.push(...await db.comments.where(key).anyOf(ids).primaryKeys());
        drop.photos.push(...await db.photos.where(key).anyOf(ids).primaryKeys());
      }
    }

    /** Queues the deletes (call inside a transaction scope). */
    function deleteKeys(drop) {
      for (const table of Object.keys(drop)) {
        if (drop[table].length) db[table].bulkDelete(drop[table]);
      }
    }

    async function dropUnseen(seen) {
      const keep = await pendingTargets();
      const drop = emptyDrop();
      for (const t of ["tasks", "runs", "shift_assignments"].concat(CHILD_TABLES)) {
        const s = seen[t] || new Set();
        drop[t] = (await db[t].toCollection().primaryKeys()).filter((id) => !s.has(id) && !keep.has(id));
      }
      await db.transaction("rw", WFDB.READ_TABLES.map((t) => db[t]), () => deleteKeys(drop));
    }

    async function pruneWindow() {
      const keep = await pendingTargets();
      const before = new Date(Date.now() - WINDOW_BACK_MS).toISOString();
      const drop = emptyDrop();
      drop.tasks = (await db.tasks.where("due_at").below(before).primaryKeys()).filter((id) => !keep.has(id));
      drop.runs = (await db.runs.where("due_at").below(before).primaryKeys()).filter((id) => !keep.has(id));
      if (drop.tasks.length || drop.runs.length) {
        await addChildren(drop, drop.tasks, drop.runs);
        await db.transaction("rw", WFDB.READ_TABLES.map((t) => db[t]), () => deleteKeys(drop));
      }
      const sent = await db.blobs.where("uploaded").equals(1).primaryKeys();
      if (sent.length) await db.blobs.bulkDelete(sent);
    }

    async function round() {
      if (Date.now() < backoffUntil) return emit({ status: "waiting" });
      await emit({ status: "syncing" });
      try {
        await uploadPhotos();
        await push();
        await pull();
        const at = new Date().toISOString();
        await WFDB.setMeta(db, "lastSyncAt", at);
        await emit({ status: "synced", lastSyncAt: at });
      } catch (e) {
        if (e instanceof AuthError) return emit({ status: "login" });
        if (e instanceof HttpError) return emit({ status: "error", error: e.code || e.status });
        // A network failure mid-round: nothing is lost, the outbox stays.
        if (!(e instanceof TypeError)) console.error("sync failed", String(e));
        return emit({ status: state.reachable ? "error" : "offline", error: "network" });
      }
    }

    let lastRoundAt = 0;

    function run() {
      lastRoundAt = Date.now();
      if (running) { again = true; return running; }
      running = round().finally(() => {
        running = null;
        if (again) { again = false; run(); }
      });
      return running;
    }

    function soon() {
      clearTimeout(timer);
      timer = setTimeout(run, 1500);
    }

    async function start() {
      state.lastSyncAt = await WFDB.getMeta(db, "lastSyncAt", null);
      window.addEventListener("online", run);
      window.addEventListener("offline", () => emit({ status: "offline", reachable: false }));
      document.addEventListener("visibilitychange", () => { if (!document.hidden) run(); });
      setInterval(() => {
        const wait = state.reachable ? INTERVAL_MS : RETRY_OFFLINE_MS;
        if (!document.hidden && Date.now() - lastRoundAt >= wait) run();
      }, RETRY_OFFLINE_MS);
      return run();
    }

    return { state: state, record: record, addPhoto: addPhoto, run: run, start: start,
             refresh: emit, clock: clock };
  }

  window.WFSync = { create: create };
})();
