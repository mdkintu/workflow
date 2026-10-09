"""Dashboard numbers (docs/01-requirements.md F3, docs/04-design.md §5.3).

Buckets are mutually exclusive and cancelled work is left out:
    done      status done ("late" = completed after its due time)
    flagged   status flagged
    overdue   pending/in progress and past due
    open      pending/in progress and not yet due
Tasks and checklist runs are counted together.

Every function here runs a fixed number of aggregate queries (conditional
Counts, values().annotate()), however much data there is — the dashboard is
checked for that in tests/test_dashboard.py. The (organisation, status,
due_at) and (organisation, location, due_at) indexes on both tables cover
the filters. Scope comes from visible_to() (docs/04-design.md §2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

from django.db.models import Count, F, Q, QuerySet
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from checklists.models import OPEN_RUN_STATUSES, ChecklistRun, ChecklistRunStatus
from organisations.models import Membership
from tasks.models import OPEN_STATUSES, Task, TaskStatus

PERIODS = {"today": _("Today"), "yesterday": _("Yesterday"), "7d": _("Last 7 days")}
GROUPS = {"staff": _("Staff"), "shift": _("Shift"), "location": _("Location")}
BUCKETS = ("done", "overdue", "flagged", "open")
OVERDUE_LIST_LIMIT = 100

_TASK_LATE = Q(completed_at_trusted__gt=F("due_at_when_completed"))
_TASK_ON_TIME = Q(completed_at_trusted__lte=F("due_at_when_completed"))
_RUN_LATE = Q(completed_at_trusted__gt=F("due_at"))
_RUN_ON_TIME = Q(completed_at_trusted__lte=F("due_at"))


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    start: datetime
    end: datetime


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.get_current_timezone())


def period_for(key: str, today: date | None = None) -> Period:
    """Periods are whole local days in the organisation's timezone."""
    today = today or timezone.localdate()
    if key == "yesterday":
        start, end = _midnight(today - timedelta(days=1)), _midnight(today)
    elif key == "7d":
        start, end = _midnight(today - timedelta(days=6)), _midnight(today + timedelta(days=1))
    else:
        key = "today"
        start, end = _midnight(today), _midnight(today + timedelta(days=1))
    return Period(key=key, label=str(PERIODS[key]), start=start, end=end)


def _scoped(model, membership: Membership, period: Period, location_id: UUID | None) -> QuerySet:
    cancelled = TaskStatus.CANCELLED if model is Task else ChecklistRunStatus.CANCELLED
    qs = (
        model.objects.visible_to(membership)
        .filter(due_at__gte=period.start, due_at__lt=period.end)
        .exclude(status=cancelled)
    )
    return qs.filter(location_id=location_id) if location_id else qs


def _counts(now: datetime, late: Q, open_statuses) -> dict:
    return {
        "done": Count("id", filter=Q(status="done")),
        "done_late": Count("id", filter=Q(status="done") & late),
        "flagged": Count("id", filter=Q(status="flagged")),
        "overdue": Count("id", filter=Q(status__in=open_statuses, due_at__lt=now)),
        "open": Count("id", filter=Q(status__in=open_statuses, due_at__gte=now)),
    }


_KEYS = ("done", "done_late", "overdue", "flagged", "open")


def summary(
    membership: Membership,
    period: Period,
    *,
    location_id: UUID | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Two queries: one aggregate over tasks, one over runs."""
    now = now or timezone.now()
    tasks = _scoped(Task, membership, period, location_id).aggregate(
        **_counts(now, _TASK_LATE, OPEN_STATUSES)
    )
    runs = _scoped(ChecklistRun, membership, period, location_id).aggregate(
        **_counts(now, _RUN_LATE, OPEN_RUN_STATUSES)
    )
    return {key: tasks[key] + runs[key] for key in _KEYS}


def _merge(rows: dict, key, label: str, counts: dict) -> None:
    row = rows.setdefault(key, {"key": key, "label": label, **dict.fromkeys(_KEYS, 0)})
    for name in _KEYS:
        row[name] += counts[name]


def breakdown(
    membership: Membership,
    period: Period,
    group: str,
    *,
    location_id: UUID | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Rows of bucket counts per staff member, shift or location — two or
    three grouped queries. By staff: finished work is credited to whoever
    completed it, open work to the assigned person; shift/location work no
    one has claimed yet, and checklist runs (usually a team's), get their own
    rows."""
    now = now or timezone.now()
    tasks = _scoped(Task, membership, period, location_id)
    runs = _scoped(ChecklistRun, membership, period, location_id)
    task_counts = _counts(now, _TASK_LATE, OPEN_STATUSES)
    run_counts = _counts(now, _RUN_LATE, OPEN_RUN_STATUSES)
    rows: dict = {}

    if group == "location":
        for r in tasks.values("location_id", "location__name").annotate(**task_counts):
            _merge(rows, r["location_id"], r["location__name"], r)
        for r in runs.values("location_id", "location__name").annotate(**run_counts):
            _merge(rows, r["location_id"], r["location__name"], r)
    elif group == "shift":
        none_label = str(_("Not on a shift"))
        for r in tasks.values("assignee_shift_id", "assignee_shift__name").annotate(**task_counts):
            _merge(rows, r["assignee_shift_id"], r["assignee_shift__name"] or none_label, r)
        for r in runs.values("shift_id", "shift__name").annotate(**run_counts):
            _merge(rows, r["shift_id"], r["shift__name"] or none_label, r)
    else:
        per_person = list(
            tasks.annotate(person=Coalesce("completed_by", "assignee_membership"))
            .values("person")
            .annotate(**task_counts)
        )
        names = {
            m.id: m.name
            for m in Membership.objects.filter(
                id__in=[r["person"] for r in per_person if r["person"]]
            ).select_related("user")
        }
        for r in per_person:
            label = names.get(r["person"]) or str(_("Shift or location tasks (unclaimed)"))
            _merge(rows, r["person"] or "unclaimed", label, r)
        run_totals = runs.aggregate(**run_counts)
        if any(run_totals.values()):
            _merge(rows, "runs", str(_("Checklists")), run_totals)

    special = {None, "unclaimed", "runs"}
    return sorted(rows.values(), key=lambda row: (row["key"] in special, row["label"]))


def overdue_items(
    membership: Membership,
    period: Period,
    *,
    location_id: UUID | None = None,
    now: datetime | None = None,
) -> dict[str, list]:
    now = now or timezone.now()
    tasks = (
        _scoped(Task, membership, period, location_id)
        .filter(status__in=OPEN_STATUSES, due_at__lt=now)
        .select_related("location", "assignee_membership__user", "assignee_shift")
        .order_by("due_at")[:OVERDUE_LIST_LIMIT]
    )
    runs = (
        _scoped(ChecklistRun, membership, period, location_id)
        .filter(status__in=OPEN_RUN_STATUSES, due_at__lt=now)
        .with_progress()
        .select_related("location", "shift")
        .order_by("due_at")[:OVERDUE_LIST_LIMIT]
    )
    return {"tasks": list(tasks), "runs": list(runs)}


def items_in_bucket(
    membership: Membership,
    period: Period,
    bucket: str,
    *,
    location_id: UUID | None = None,
    now: datetime | None = None,
) -> dict[str, list]:
    """The drill-down behind each count."""
    now = now or timezone.now()
    filters = {
        "done": (Q(status="done"), Q(status="done")),
        "flagged": (Q(status="flagged"), Q(status="flagged")),
        "overdue": (
            Q(status__in=OPEN_STATUSES, due_at__lt=now),
            Q(status__in=OPEN_RUN_STATUSES, due_at__lt=now),
        ),
        "open": (
            Q(status__in=OPEN_STATUSES, due_at__gte=now),
            Q(status__in=OPEN_RUN_STATUSES, due_at__gte=now),
        ),
    }
    task_q, run_q = filters.get(bucket, filters["overdue"])
    tasks = (
        _scoped(Task, membership, period, location_id)
        .filter(task_q)
        .select_related("location", "assignee_membership__user", "assignee_shift")
        .order_by("due_at")[:OVERDUE_LIST_LIMIT]
    )
    runs = (
        _scoped(ChecklistRun, membership, period, location_id)
        .filter(run_q)
        .with_progress()
        .select_related("location", "shift")
        .order_by("due_at")[:OVERDUE_LIST_LIMIT]
    )
    return {"tasks": list(tasks), "runs": list(runs)}


def _pct(part: int, whole: int) -> int | None:
    return round(100 * part / whole) if whole else None


def trend(
    membership: Membership,
    *,
    days: int = 30,
    today: date | None = None,
    location_id: UUID | None = None,
) -> dict:
    """Completion rate per full day for the last `days` days (today isn't
    over yet, so it's left out), plus 7- and 30-day rates. A day with nothing
    due has rate None — a gap, not 0 %. Two grouped queries."""
    today = today or timezone.localdate()
    tz = timezone.get_current_timezone()
    window = Period(
        key="trend", label="", start=_midnight(today - timedelta(days=days)), end=_midnight(today)
    )

    def daily(model, on_time: Q) -> QuerySet:
        return (
            _scoped(model, membership, window, location_id)
            .annotate(day=TruncDate("due_at", tzinfo=tz))
            .values("day")
            .annotate(
                total=Count("id"),
                done=Count("id", filter=Q(status="done")),
                on_time=Count("id", filter=Q(status="done") & on_time),
            )
        )

    per_day: dict[date, dict] = {}
    for rows in (daily(Task, _TASK_ON_TIME), daily(ChecklistRun, _RUN_ON_TIME)):
        for r in rows:
            totals = per_day.setdefault(r["day"], {"total": 0, "done": 0, "on_time": 0})
            for name in ("total", "done", "on_time"):
                totals[name] += r[name]

    series = []
    for offset in range(days, 0, -1):
        day = today - timedelta(days=offset)
        t = per_day.get(day, {"total": 0, "done": 0, "on_time": 0})
        series.append({"day": day, **t, "rate": _pct(t["done"], t["total"])})

    def rate(points: list[dict], field: str) -> int | None:
        return _pct(sum(p[field] for p in points), sum(p["total"] for p in points))

    last_7 = series[-7:]
    return {
        "series": series,
        "rate_7": rate(last_7, "done"),
        "on_time_7": rate(last_7, "on_time"),
        "rate_30": rate(series, "done"),
        "on_time_30": rate(series, "on_time"),
    }
