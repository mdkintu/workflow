// Local store for the field app (docs/02-architecture.md §4.3).
// One IndexedDB database per membership, so switching organisation or
// sharing a phone never mixes data. Read models mirror the pull tables;
// `outbox` holds changes made on the phone until the server has them;
// `blobs` holds compressed photos until they are uploaded.
(function () {
  "use strict";

  const READ_TABLES = [
    "locations", "people", "shifts", "shift_assignments", "tasks", "runs",
    "run_items", "run_item_ticks", "comments", "photos",
  ];

  function open(membershipId) {
    const db = new Dexie("workflow-" + membershipId);
    db.version(1).stores({
      locations: "id",
      people: "id",
      shifts: "id",
      shift_assignments: "id, date",
      tasks: "id, due_at",
      runs: "id, due_at",
      run_items: "id, run_id",
      run_item_ticks: "id, run_item_id",
      comments: "id, task_id, run_id",
      photos: "id, task_id, run_id",
      outbox: "mutation_id, seq",
      blobs: "id, uploaded",
      problems: "mutation_id",
      meta: "key",
    });
    return db;
  }

  async function getMeta(db, key, fallback) {
    const row = await db.meta.get(key);
    return row === undefined ? fallback : row.value;
  }

  function setMeta(db, key, value) {
    return db.meta.put({ key: key, value: value });
  }

  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    // Older Android WebViews: RFC 4122 v4 from getRandomValues.
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
    return h.slice(0, 8) + "-" + h.slice(8, 12) + "-" + h.slice(12, 16) + "-" +
      h.slice(16, 20) + "-" + h.slice(20);
  }

  window.WFDB = { open: open, getMeta: getMeta, setMeta: setMeta, uuid: uuid, READ_TABLES: READ_TABLES };
})();
