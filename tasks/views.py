"""Task screens, server-rendered with HTMX (docs/04-design.md §3 S4/S5/S8,
§4.1). The offline field-app versions of "My tasks" and task detail come
with the PWA work; these online screens stay for supervisors and as the
fallback.

Access rules, in order: the tenant-scoped manager (another organisation's
task is a 404), then visibility (docs/04-design.md §2 scopes — same
organisation but not yours is a 403), then the per-action permission.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from checklists.models import OPEN_RUN_STATUSES, ChecklistRun, ChecklistRunStatus
from organisations.models import Location, Membership, Shift, ShiftAssignment
from organisations.permissions import Role, can, role_required
from tasks import services
from tasks.forms import FLAG_REASONS, TaskForm
from tasks.models import OPEN_STATUSES, Task, TaskComment, TaskPhoto, TaskStatus
from tasks.photos import PhotoError, photo_response, store_task_photo
from tasks.transitions import Action, allowed_actions, apply

MY_TASKS_LOOKBACK = timedelta(days=3)  # how far back overdue work still shows
NOW_WINDOW = timedelta(hours=2)
LIST_LIMIT = 200
BUCKETS = ("all", "open", "overdue", "flagged", "done", "cancelled")


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _get_visible_task(request: HttpRequest, task_id: UUID) -> Task:
    task = get_object_or_404(
        Task.objects.select_related(
            "location", "assignee_membership__user", "assignee_shift", "created_by__user"
        ),
        pk=task_id,
    )
    if not Task.objects.visible_to(request.membership).filter(pk=task.pk).exists():
        raise PermissionDenied
    return task


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=timezone.get_current_timezone())
    return start, start + timedelta(days=1)


# --- lists ---


@login_required
def my_tasks(request: HttpRequest) -> HttpResponse:
    """S4, online version: my work, grouped Overdue / Flagged / Now / Later
    today / Tomorrow / Done today."""
    membership = request.membership
    now = timezone.now()
    today_start, today_end = _day_bounds(timezone.localdate())
    tomorrow_end = today_end + timedelta(days=1)

    open_window = Q(due_at__gte=now - MY_TASKS_LOOKBACK, due_at__lt=tomorrow_end)
    tasks = list(
        Task.objects.mine(membership)
        .filter(
            (Q(status__in=[*OPEN_STATUSES, TaskStatus.FLAGGED]) & open_window)
            | Q(status=TaskStatus.DONE, completed_received_at__gte=today_start)
        )
        .select_related("location", "assignee_membership__user", "assignee_shift")
    )
    runs = list(
        ChecklistRun.objects.mine(membership)
        .with_progress()
        .filter(
            (Q(status__in=[*OPEN_RUN_STATUSES, ChecklistRunStatus.FLAGGED]) & open_window)
            | Q(status=ChecklistRunStatus.DONE, completed_at_trusted__gte=today_start)
        )
        .select_related("location", "shift")
    )
    for task in tasks:
        task.kind = "task"
    for run in runs:
        run.kind = "run"

    # Tasks and checklist runs share status names, so they group the same way
    # (docs/04-design.md §3 S4: "mixed together and sorted by due time").
    groups = {key: [] for key in ("overdue", "flagged", "now", "later", "tomorrow", "done")}
    for entry in sorted([*tasks, *runs], key=lambda e: e.due_at):
        if entry.status == TaskStatus.DONE:
            groups["done"].append(entry)
        elif entry.status == TaskStatus.FLAGGED:
            groups["flagged"].append(entry)
        elif entry.due_at < now:
            groups["overdue"].append(entry)
        elif entry.due_at < now + NOW_WINDOW:
            groups["now"].append(entry)
        elif entry.due_at < today_end:
            groups["later"].append(entry)
        else:
            groups["tomorrow"].append(entry)

    labels = {
        "overdue": _("Overdue"),
        "flagged": _("Flagged"),
        "now": _("Now"),
        "later": _("Later today"),
        "tomorrow": _("Tomorrow"),
        "done": _("Done today"),
    }
    shifts_today = ShiftAssignment.objects.filter(
        membership=membership, date=timezone.localdate()
    ).select_related("shift__location")
    return render(
        request,
        "tasks/my_tasks.html",
        {
            "groups": [(key, labels[key], groups[key]) for key in labels if groups[key]],
            "shifts_today": shifts_today,
        },
    )


@login_required
@role_required(Role.STAFF)
def task_list(request: HttpRequest) -> HttpResponse:
    """Staff+ list with filters (bucket, location, day). Filtering is an
    HTMX swap of just the rows."""
    now = timezone.now()
    view = "board" if request.GET.get("view") == "board" else "list"
    bucket = request.GET.get("bucket", "all")
    if bucket not in BUCKETS or view == "board":
        bucket = "all"  # the board's columns are the statuses
    try:
        day = date.fromisoformat(request.GET.get("day", ""))
    except ValueError:
        day = timezone.localdate()
    try:
        location_id = UUID(request.GET.get("loc", ""))
    except ValueError:
        location_id = None

    tasks = (
        Task.objects.visible_to(request.membership)
        .annotate_overdue()
        .select_related("location", "assignee_membership__user", "assignee_shift")
    )
    if location_id:
        tasks = tasks.filter(location_id=location_id)
    if bucket == "overdue":
        # Overdue work is shown whatever day it was due.
        tasks = tasks.filter(status__in=OPEN_STATUSES, due_at__lt=now)
    else:
        start, end = _day_bounds(day)
        tasks = tasks.filter(due_at__gte=start, due_at__lt=end)
        statuses = {
            "open": OPEN_STATUSES,
            "flagged": [TaskStatus.FLAGGED],
            "done": [TaskStatus.DONE],
            "cancelled": [TaskStatus.CANCELLED],
        }
        if bucket in statuses:
            tasks = tasks.filter(status__in=statuses[bucket])
    tasks = list(tasks.order_by("due_at")[:LIST_LIMIT])

    context = {
        "view": view,
        "columns": services.board_columns(tasks, request.membership) if view == "board" else [],
        "tasks": tasks,
        "bucket": bucket,
        "day": day,
        "location_id": location_id,
        "locations": Location.objects.filter(is_active=True),
        "buckets": BUCKETS,
        "can_create": can(request.membership, "task.create"),
        "can_create_self": can(request.membership, "task.create_self"),
    }
    if _is_htmx(request):
        template = "tasks/_task_board.html" if view == "board" else "tasks/_task_rows.html"
    else:
        template = "tasks/task_list.html"
    return render(request, template, context)


# --- create / edit ---


def _assignee_context(location_id, day: date | None, person=None, shift=None) -> dict:
    """People (those rostered at the location that day first) and the
    location's shifts, for the assignee pickers."""
    people = list(
        Membership.objects.filter(is_active=True).select_related("user").order_by("user__name")
    )
    shifts, on_shift = [], set()
    if location_id:
        shifts = list(Shift.objects.filter(location_id=location_id, is_active=True))
        if day:
            on_shift = set(
                ShiftAssignment.objects.filter(
                    shift__location_id=location_id, date=day
                ).values_list("membership_id", flat=True)
            )
    for person_option in people:
        person_option.on_shift = person_option.id in on_shift
    people.sort(key=lambda p: (not p.on_shift, p.name))
    return {
        "people": people,
        "shifts": shifts,
        "selected_person": str(person or ""),
        "selected_shift": str(shift or ""),
    }


def _form_context(form: TaskForm) -> dict:
    def value(name):
        return form[name].value() or ""

    location_id = value("location")
    try:
        day = date.fromisoformat(str(value("due_date")))
    except ValueError:
        day = None
    return {
        "form": form,
        **_assignee_context(
            location_id, day, value("assignee_membership"), value("assignee_shift")
        ),
    }


@login_required
@role_required(Role.SUPERVISOR)
def task_create(request: HttpRequest) -> HttpResponse:
    membership = request.membership
    if not can(membership, "task.create"):
        raise PermissionDenied
    initial = {
        "assign_type": "person",
        "due_date": timezone.localdate(),
        "photo_required": request.organisation.photo_required_default,
    }
    form = TaskForm(request.POST or None, membership=membership, initial=initial)
    if request.method == "POST" and form.is_valid():
        task = services.create_task(actor=membership, fields=form.task_fields())
        messages.success(request, _("Task created."))
        return redirect("tasks:detail", task_id=task.id)
    return render(request, "tasks/task_form.html", {**_form_context(form), "task": None})


@login_required
@role_required(Role.SUPERVISOR)
def task_edit(request: HttpRequest, task_id: UUID) -> HttpResponse:
    """Edit and reassign (docs/04-design.md §5.2: done/cancelled are final)."""
    task = _get_visible_task(request, task_id)
    if not can(request.membership, "task.edit", obj=task):
        raise PermissionDenied
    if task.status not in services.EDITABLE_STATUSES:
        return render(request, "tasks/task_not_editable.html", {"task": task}, status=409)

    form = TaskForm(request.POST or None, membership=request.membership, instance=task)
    if request.method == "POST" and form.is_valid():
        services.update_task(actor=request.membership, task=task, fields=form.task_fields())
        messages.success(request, _("Task updated."))
        return redirect("tasks:detail", task_id=task.id)
    return render(request, "tasks/task_form.html", {**_form_context(form), "task": task})


@login_required
@role_required(Role.SUPERVISOR)
def assignee_options(request: HttpRequest) -> HttpResponse:
    """HTMX: refreshes the person/shift pickers when the location or day
    changes on the task form."""
    try:
        location_id = UUID(request.GET.get("location", ""))
    except ValueError:
        location_id = None
    try:
        day = date.fromisoformat(request.GET.get("due_date", ""))
    except ValueError:
        day = None
    context = _assignee_context(
        location_id,
        day,
        request.GET.get("assignee_membership", ""),
        request.GET.get("assignee_shift", ""),
    )
    return render(request, "tasks/_assignees.html", context)


# --- detail, actions, photos, comments ---


def _panel_context(request: HttpRequest, task: Task, error: str | None = None) -> dict:
    membership = request.membership
    return {
        "task": task,
        "actions": allowed_actions(task, membership),
        "photos": task.photos.filter(linked_at__isnull=False).order_by("created_at"),
        "can_upload": (
            can(membership, "task.complete", obj=task) and task.status != TaskStatus.CANCELLED
        ),
        "can_edit": (
            can(membership, "task.edit", obj=task) and task.status in services.EDITABLE_STATUSES
        ),
        "flag_reasons": FLAG_REASONS,
        "error": error,
    }


def comments_context(request: HttpRequest, parent, error: str | None = None) -> dict:
    """For tasks/_comments.html, on a task or a checklist run."""
    comments = list(services.visible_comments(parent))
    window_start = timezone.now() - services.COMMENT_DELETE_WINDOW
    for comment in comments:
        comment.can_delete = (
            comment.author_id == request.membership.id and comment.created_at >= window_start
        )
    is_task = isinstance(parent, Task)
    return {
        "comment_parent": parent,
        "comments": comments,
        "comment_error": error,
        "max_length": services.COMMENT_MAX_LENGTH,
        "comment_add_url": "tasks:comment-add" if is_task else "checklists:run-comment-add",
        "comment_delete_url": "tasks:comment-delete"
        if is_task
        else "checklists:run-comment-delete",
    }


def _render_panel(request: HttpRequest, task: Task, error: str | None = None) -> HttpResponse:
    return render(request, "tasks/_panel.html", _panel_context(request, task, error))


@login_required
def task_detail(request: HttpRequest, task_id: UUID) -> HttpResponse:
    task = _get_visible_task(request, task_id)
    return render(
        request,
        "tasks/task_detail.html",
        {**_panel_context(request, task), **comments_context(request, task)},
    )


@login_required
@require_POST
def task_action(request: HttpRequest, task_id: UUID, action: Action) -> HttpResponse:
    task = _get_visible_task(request, task_id)
    result = apply(
        task,
        action,
        request.membership,
        reason=request.POST.get("reason", ""),
        note=request.POST.get("note", ""),
    )
    if result.code == "forbidden":
        raise PermissionDenied
    if result.code == "not_found":
        raise Http404

    error = None if result.ok else result.message
    if _is_htmx(request):
        return _render_panel(request, task, error)
    if error:
        messages.error(request, error)
    # The task board posts here and wants to come back to itself (F1.6).
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("tasks:detail", task_id=task.id)


@login_required
@require_POST
def task_photo_upload(request: HttpRequest, task_id: UUID) -> HttpResponse:
    task = _get_visible_task(request, task_id)
    if not can(request.membership, "task.complete", obj=task):
        raise PermissionDenied
    if task.status == TaskStatus.CANCELLED:
        raise PermissionDenied

    error = None
    upload = request.FILES.get("photo")
    if upload is None:
        error = _("Choose a photo first.")
    else:
        try:
            store_task_photo(task=task, uploaded_by=request.membership, upload=upload)
        except PhotoError as exc:
            error = str(exc)

    if _is_htmx(request):
        return _render_panel(request, task, error)
    if error:
        context = {**_panel_context(request, task, error), **comments_context(request, task)}
        return render(request, "tasks/task_detail.html", context, status=400)
    return redirect("tasks:detail", task_id=task.id)


@login_required
@require_POST
def comment_add(request: HttpRequest, task_id: UUID) -> HttpResponse:
    task = _get_visible_task(request, task_id)
    if not can(request.membership, "comment.add", obj=task):
        raise PermissionDenied
    error = None
    try:
        services.add_comment(actor=request.membership, task=task, body=request.POST.get("body", ""))
    except services.CommentError as exc:
        error = str(exc)
    if _is_htmx(request):
        return render(request, "tasks/_comments.html", comments_context(request, task, error))
    if error:
        messages.error(request, error)
    return redirect("tasks:detail", task_id=task.id)


@login_required
@require_POST
def comment_delete(request: HttpRequest, task_id: UUID, comment_id: UUID) -> HttpResponse:
    task = _get_visible_task(request, task_id)
    comment = get_object_or_404(TaskComment, pk=comment_id, task=task, deleted_at__isnull=True)
    services.delete_comment(actor=request.membership, comment=comment)
    if _is_htmx(request):
        return render(request, "tasks/_comments.html", comments_context(request, task))
    return redirect("tasks:detail", task_id=task.id)


@login_required
def photo_file(request: HttpRequest, photo_id: UUID, thumb: bool = False) -> HttpResponse:
    """/media/p/<id>: never a public URL — the tenant-scoped lookup (another
    organisation's photo is a 404), then the task's visibility (403)."""
    photo = get_object_or_404(TaskPhoto, pk=photo_id)
    membership = request.membership
    if photo.task_id is not None:
        visible = Task.objects.visible_to(membership).filter(pk=photo.task_id).exists()
    elif photo.checklist_run_id is not None:
        visible = (
            ChecklistRun.objects.visible_to(membership).filter(pk=photo.checklist_run_id).exists()
        )
    else:
        raise Http404
    if not (can(membership, "photo.view", obj=photo) and visible):
        raise PermissionDenied
    return photo_response(photo, thumb=thumb)


@login_required
def task_create_self(request: HttpRequest) -> HttpResponse:
    """Staff creates a one-off task for themselves. Auto-assigns to current
    person and uses the organisation's default location."""
    from tasks.forms import SelfTaskRecurringForm

    membership = request.membership
    if not can(membership, "task.create_self"):
        raise PermissionDenied

    if request.method == "POST":
        form = SelfTaskRecurringForm(request.POST, membership=membership)
        if form.is_valid():
            # Create the one-off self-task
            fields = form.task_fields()
            task = Task.objects.create(created_by=membership, **fields)
            messages.success(request, _("Task created"))

            # If recurring, create the rule and first occurrence
            if form.cleaned_data.get("recurs"):
                from tasks.models import TaskRecurrenceRule

                rule = TaskRecurrenceRule.objects.create(
                    organisation=membership.organisation,
                    created_by=membership,
                    **form.rule_fields(),
                )
                task.recurrence_rule = rule
                task.occurrence_start = timezone.now().replace(
                    hour=int(form.cleaned_data["due_time"].hour),
                    minute=int(form.cleaned_data["due_time"].minute),
                    second=0,
                    microsecond=0,
                )
                task.save()

            return redirect("tasks:list", bucket="open")

    else:
        form = SelfTaskRecurringForm(membership=membership)

    return render(request, "tasks/task_form_self.html", {"form": form})
