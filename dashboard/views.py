"""S7 Manager dashboard (docs/01-requirements.md F3, docs/04-design.md §3).

The page shows the period's counts, a staff/shift/location breakdown and the
overdue list (the `_summary.html` partial, which HTMX refreshes every 60 s
while the tab is visible), plus 7/30-day completion trends. Every count links
to a drill-down list. Overdue tasks can be reassigned in one tap.
"""

from __future__ import annotations

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from dashboard import queries
from dashboard.charts import completion_chart
from organisations.models import Location, Membership, ShiftAssignment
from organisations.permissions import Role, can, role_required
from tasks import services as task_services
from tasks.models import Task


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _require_dashboard(request: HttpRequest) -> None:
    if not can(request.membership, "dashboard.view"):
        raise PermissionDenied


def _filters(request: HttpRequest) -> dict:
    period = queries.period_for(request.GET.get("range", "today"))
    group = request.GET.get("group", "staff")
    if group not in queries.GROUPS:
        group = "staff"
    try:
        location_id = UUID(request.GET.get("loc", ""))
    except ValueError:
        location_id = None
    return {"period": period, "group": group, "location_id": location_id}


def _people_for_picker() -> list[Membership]:
    """Everyone active, those on any shift today first (one-tap reassign)."""
    on_shift = set(
        ShiftAssignment.objects.filter(date=timezone.localdate()).values_list(
            "membership_id", flat=True
        )
    )
    people = list(Membership.objects.filter(is_active=True).select_related("user"))
    for person in people:
        person.on_shift = person.id in on_shift
    return sorted(people, key=lambda p: (not p.on_shift, p.name))


def _summary_context(request: HttpRequest, filters: dict) -> dict:
    membership = request.membership
    now = timezone.now()
    period, location_id = filters["period"], filters["location_id"]
    overdue = queries.overdue_items(membership, period, location_id=location_id, now=now)
    return {
        **filters,
        "group_label": queries.GROUPS[filters["group"]],
        "overdue": overdue,
        "overdue_count": len(overdue["tasks"]) + len(overdue["runs"]),
        "counts": queries.summary(membership, period, location_id=location_id, now=now),
        "rows": queries.breakdown(
            membership, period, filters["group"], location_id=location_id, now=now
        ),
        "people": _people_for_picker(),
        "can_reassign": can(membership, "task.edit"),
        "groups": queries.GROUPS,
        "updated_at": now,
    }


@login_required
@role_required(Role.SUPERVISOR)
def dashboard(request: HttpRequest) -> HttpResponse:
    _require_dashboard(request)
    filters = _filters(request)
    trend = queries.trend(request.membership, location_id=filters["location_id"])
    return render(
        request,
        "dashboard/dashboard.html",
        {
            **_summary_context(request, filters),
            "periods": queries.PERIODS,
            "locations": Location.objects.filter(is_active=True),
            "trend": trend,
            "chart": completion_chart(trend["series"]),
        },
    )


@login_required
@role_required(Role.SUPERVISOR)
def summary_partial(request: HttpRequest) -> HttpResponse:
    _require_dashboard(request)
    return render(request, "dashboard/_summary.html", _summary_context(request, _filters(request)))


@login_required
@role_required(Role.SUPERVISOR)
def drill_down(request: HttpRequest) -> HttpResponse:
    _require_dashboard(request)
    filters = _filters(request)
    bucket = request.GET.get("bucket", "overdue")
    if bucket not in queries.BUCKETS:
        bucket = "overdue"
    items = queries.items_in_bucket(
        request.membership, filters["period"], bucket, location_id=filters["location_id"]
    )
    return render(
        request,
        "dashboard/drill_down.html",
        {**filters, "bucket": bucket, "tasks": items["tasks"], "runs": items["runs"]},
    )


@login_required
@role_required(Role.SUPERVISOR)
@require_POST
def reassign(request: HttpRequest, task_id: UUID) -> HttpResponse:
    """One-tap reassign of an overdue task to one person (docs/01 F3)."""
    task = get_object_or_404(
        Task.objects.select_related("location", "assignee_membership__user", "assignee_shift"),
        pk=task_id,
    )
    visible = Task.objects.visible_to(request.membership).filter(pk=task.pk).exists()
    if not (visible and can(request.membership, "task.edit", obj=task)):
        raise PermissionDenied
    try:
        person = get_object_or_404(
            Membership, pk=UUID(request.POST.get("membership", "")), is_active=True
        )
    except ValueError as exc:
        raise Http404 from exc
    if task.status not in task_services.EDITABLE_STATUSES:
        return HttpResponse(status=409)

    fields = {name: getattr(task, name) for name in task_services.EDITABLE_FIELDS}
    fields.update(
        assignee_membership=person, assignee_shift=None, assignee_location=False, shift_date=None
    )
    task_services.update_task(actor=request.membership, task=task, fields=fields)

    if not _is_htmx(request):
        return redirect("dashboard:dashboard")
    return render(
        request,
        "dashboard/_overdue_task.html",
        {
            "task": task,
            "people": _people_for_picker(),
            "can_reassign": True,
            "just_reassigned": True,
        },
    )
