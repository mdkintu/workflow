"""GET /api/sync/pull (docs/04-design.md §4.4, docs/02-architecture.md §4.4,
ADR-03).

Every synced row carries `updated_seq` from one PostgreSQL sequence, set by a
trigger on insert and update. A pull returns what this membership may see,
changed since the client's cursor, in sequence order and in pages.

Safe high-water mark: a sequence number is taken when a row is written, but
the row only becomes visible when its transaction commits — so a slower
transaction can commit seq 10 after a faster one committed seq 11. Advancing
the cursor past 11 would then skip 10 for ever. A pull therefore stops just
before the oldest row written in the last LAG: a transaction still open
after LAG is the only way to be skipped, and every transaction in this app
is short. The phone also does a full refresh every few hours (sync.js),
which heals anything missed and roster removals (hard deletes).

Other rules:
- A task or run in the page comes with *all* its children (comments, photos,
  items, ticks), even older ones, so work newly visible to someone (e.g.
  reassigned to them) arrives complete.
- Tombstones: soft-deleted comments, and tasks/runs changed since the cursor
  that this person can no longer see.
- A roster row of mine in the page brings that shift's tasks and runs with
  it: they may be older than the cursor and only now visible to me.
- `reset: true` asks the phone to wipe its copy and pull from 0 when its
  cursor is ahead of the server (the server was restored from a backup).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from django.db.models import Max, Min, Q, QuerySet

from checklists.models import ChecklistRun, ChecklistRunItem, ChecklistRunItemTick
from organisations.models import Location, Membership, Shift, ShiftAssignment
from sync import serializers
from tasks.models import Task, TaskComment, TaskPhoto

LAG = timedelta(seconds=10)
WINDOW_BACK = timedelta(days=3)
WINDOW_AHEAD = timedelta(days=2)
DEFAULT_LIMIT = 500
MAX_LIMIT = 1000

SYNCED_MODELS = (
    Location,
    Membership,
    Shift,
    ShiftAssignment,
    Task,
    TaskPhoto,
    TaskComment,
    ChecklistRun,
    ChecklistRunItem,
    ChecklistRunItemTick,
)
TABLES = (
    "locations",
    "people",
    "shifts",
    "shift_assignments",
    "tasks",
    "runs",
    "run_items",
    "run_item_ticks",
    "comments",
    "photos",
)
_SERIALIZERS = {
    "locations": serializers.location,
    "people": serializers.person,
    "shifts": serializers.shift,
    "shift_assignments": serializers.shift_assignment,
    "tasks": serializers.task,
    "runs": serializers.run,
    "run_items": serializers.run_item,
    "run_item_ticks": serializers.run_item_tick,
    "comments": serializers.comment,
    "photos": serializers.photo,
}


def high_water(now: datetime, cursor: int) -> tuple[int, int]:
    """(safe high-water mark, newest sequence number) for the current
    organisation — see the module docstring. Only rows above the cursor
    matter: the phone already has everything at or below it."""
    newest, oldest_recent = 0, None
    recent = Q(updated_at__gt=now - LAG, updated_seq__gt=cursor)
    for model in SYNCED_MODELS:
        stats = model.objects.aggregate(
            newest=Max("updated_seq"), oldest_recent=Min("updated_seq", filter=recent)
        )
        newest = max(newest, stats["newest"] or 0)
        if stats["oldest_recent"] is not None:
            oldest_recent = (
                stats["oldest_recent"]
                if oldest_recent is None
                else min(oldest_recent, stats["oldest_recent"])
            )
    safe = newest if oldest_recent is None else oldest_recent - 1
    return safe, newest


def _empty() -> dict:
    return {table: [] for table in TABLES}


def build_pull(membership: Membership, cursor: int, limit: int, now: datetime) -> dict:
    """Runs inside the request's tenant context."""
    safe, newest = high_water(now, cursor)
    if cursor > newest:
        return {
            "cursor": 0,
            "has_more": False,
            "reset": True,
            "changes": _empty(),
            "tombstones": [],
        }

    tasks = Task.objects.visible_to(membership).filter(
        due_at__gte=now - WINDOW_BACK, due_at__lte=now + WINDOW_AHEAD
    )
    runs = ChecklistRun.objects.visible_to(membership).filter(
        due_at__gte=now - WINDOW_BACK, due_at__lte=now + WINDOW_AHEAD
    )
    task_ids, run_ids = tasks.values("id"), runs.values("id")
    window_days = ((now - WINDOW_BACK).date(), (now + WINDOW_AHEAD).date())

    sources: dict[str, QuerySet] = {
        "locations": Location.objects.all(),
        "people": Membership.objects.select_related("user"),
        "shifts": Shift.objects.all(),
        "shift_assignments": ShiftAssignment.objects.filter(
            membership=membership, date__range=window_days
        ),
        "tasks": tasks,
        "runs": runs.select_related("rule"),
        "run_items": ChecklistRunItem.objects.filter(run_id__in=run_ids),
        "run_item_ticks": ChecklistRunItemTick.objects.filter(run_item__run_id__in=run_ids),
        "comments": TaskComment.objects.filter(
            Q(task_id__in=task_ids) | Q(checklist_run_id__in=run_ids), deleted_at__isnull=True
        ),
        "photos": TaskPhoto.objects.filter(
            Q(task_id__in=task_ids) | Q(checklist_run_id__in=run_ids), linked_at__isnull=False
        ),
    }

    # Each table's first limit+1 changes; the smallest `limit` of them all,
    # by sequence number, are the page.
    candidates = []
    for table, qs in sources.items():
        rows = qs.filter(updated_seq__gt=cursor, updated_seq__lte=safe).order_by("updated_seq")
        candidates += [(row.updated_seq, table, row) for row in rows[: limit + 1]]
    candidates.sort(key=lambda c: c[0])
    page, has_more = candidates[:limit], len(candidates) > limit
    new_cursor = page[-1][0] if has_more else max(cursor, safe)

    changes = _empty()
    seen: set[tuple[str, object]] = set()

    def add(table: str, row) -> None:
        if (table, row.id) not in seen:
            seen.add((table, row.id))
            changes[table].append(_SERIALIZERS[table](row))

    for _seq, table, row in page:
        add(table, row)

    # The work of a shift I've just been rostered on, whatever its age.
    rostered = [(row.shift_id, row.date) for _s, table, row in page if table == "shift_assignments"]
    if rostered:
        task_q, run_q = Q(pk__in=[]), Q(pk__in=[])
        for shift_id, day in rostered:
            task_q |= Q(assignee_shift_id=shift_id, shift_date=day)
            run_q |= Q(shift_id=shift_id, shift_date=day)
        for row in tasks.filter(task_q):
            add("tasks", row)
        for row in runs.select_related("rule").filter(run_q):
            add("runs", row)

    # Children of every task/run sent, whatever their age.
    page_tasks = [UUID(row["id"]) for row in changes["tasks"]]
    page_runs = [UUID(row["id"]) for row in changes["runs"]]
    if page_tasks or page_runs:
        for row in TaskComment.objects.filter(
            Q(task_id__in=page_tasks) | Q(checklist_run_id__in=page_runs), deleted_at__isnull=True
        ):
            add("comments", row)
        for row in TaskPhoto.objects.filter(
            Q(task_id__in=page_tasks) | Q(checklist_run_id__in=page_runs), linked_at__isnull=False
        ):
            add("photos", row)
        for row in ChecklistRunItem.objects.filter(run_id__in=page_runs):
            add("run_items", row)
        for row in ChecklistRunItemTick.objects.filter(run_item__run_id__in=page_runs):
            add("run_item_ticks", row)

    tombstones = []
    if cursor > 0:
        in_range = {"updated_seq__gt": cursor, "updated_seq__lte": new_cursor}
        for table, model, visible in (("tasks", Task, tasks), ("runs", ChecklistRun, runs)):
            gone = model.objects.filter(**in_range).exclude(id__in=visible.values("id"))
            tombstones += [{"type": table, "id": str(i)} for i in gone.values_list("id", flat=True)]
        deleted = TaskComment.objects.filter(**in_range, deleted_at__isnull=False)
        tombstones += [
            {"type": "comments", "id": str(i)} for i in deleted.values_list("id", flat=True)
        ]

    return {
        "cursor": new_cursor,
        "has_more": has_more,
        "reset": False,
        "changes": changes,
        "tombstones": tombstones,
    }
