"""Checklist screens (docs/04-design.md §3 S6, §4.1 /checklists/...).

Template editing is Manager+ (`template.manage`). Runs follow the same access
rules as tasks: another organisation's run is a 404, same organisation but
not yours is a 403, then the per-action permission (checklists.runs).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from checklists import runs, services
from checklists.forms import ItemForm, RuleForm, TemplateForm
from checklists.models import (
    ChecklistItem,
    ChecklistRun,
    ChecklistRunItem,
    ChecklistRunItemTick,
    ChecklistTemplate,
    RecurrenceRule,
)
from organisations.forms import WEEKDAY_CHOICES
from organisations.permissions import Role, can, role_required
from tasks import services as task_services
from tasks.forms import FLAG_REASONS
from tasks.models import TaskComment
from tasks.photos import PhotoError, store_task_photo
from tasks.views import comments_context


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _require_manage(request: HttpRequest) -> None:
    if not can(request.membership, "template.manage"):
        raise PermissionDenied


# --- templates (Manager+) ---


@login_required
@role_required(Role.MANAGER)
def template_list(request: HttpRequest) -> HttpResponse:
    _require_manage(request)
    templates = (
        ChecklistTemplate.objects.select_related("location", "shift")
        .annotate(
            item_count=Count("items", filter=Q(items__is_active=True), distinct=True),
            rule_count=Count(
                "recurrence_rules", filter=Q(recurrence_rules__is_active=True), distinct=True
            ),
        )
        .order_by("-is_active", "location__name", "name")
    )
    return render(request, "checklists/template_list.html", {"templates": templates})


@login_required
@role_required(Role.MANAGER)
def template_create(request: HttpRequest) -> HttpResponse:
    _require_manage(request)
    form = TemplateForm(request.POST or None, initial={"is_active": True})
    if request.method == "POST" and form.is_valid():
        template = services.create_template(
            actor=request.membership,
            name=form.cleaned_data["name"].strip(),
            location=form.cleaned_data["location"],
            shift=form.cleaned_data["shift"],
        )
        return redirect("checklists:template", template_id=template.id)
    return render(request, "checklists/template_form.html", {"form": form})


def _editor_context(template: ChecklistTemplate, **extra) -> dict:
    day_names = dict(WEEKDAY_CHOICES)
    rules = list(template.recurrence_rules.order_by("-is_active", "created_at"))
    for rule in rules:
        rule.day_names = [day_names[d] for d in rule.weekdays]
    return {
        "template": template,
        "items": ChecklistItem.objects.filter(template=template, is_active=True).order_by("order"),
        "rules": rules,
        "item_form": ItemForm(),
        **extra,
    }


@login_required
@role_required(Role.MANAGER)
def template_edit(request: HttpRequest, template_id: UUID) -> HttpResponse:
    _require_manage(request)
    template = get_object_or_404(
        ChecklistTemplate.objects.select_related("location", "shift"), pk=template_id
    )
    form = TemplateForm(
        request.POST or None,
        instance=template,
        initial={
            "name": template.name,
            "location": template.location_id,
            "shift": template.shift_id,
            "is_active": template.is_active,
        },
    )
    if request.method == "POST" and form.is_valid():
        services.update_template(
            actor=request.membership,
            template=template,
            name=form.cleaned_data["name"].strip(),
            shift=form.cleaned_data["shift"],
            is_active=form.cleaned_data["is_active"],
        )
        messages.success(request, _("Saved."))
        return redirect("checklists:template", template_id=template.id)
    rule_form = RuleForm(
        template=template,
        initial={"kind": "daily", "starts_on": timezone.localdate(), "due_offset_min": 60},
    )
    return render(
        request,
        "checklists/template_edit.html",
        _editor_context(template, form=form, rule_form=rule_form),
    )


def _template_for_item(request: HttpRequest, template_id: UUID, item_id: UUID | None = None):
    _require_manage(request)
    template = get_object_or_404(ChecklistTemplate, pk=template_id)
    item = None
    if item_id is not None:
        item = get_object_or_404(ChecklistItem, pk=item_id, template=template, is_active=True)
    return template, item


def _items_response(request: HttpRequest, template: ChecklistTemplate, error=None) -> HttpResponse:
    if _is_htmx(request):
        return render(
            request, "checklists/_items.html", _editor_context(template, item_error=error)
        )
    if error:
        messages.error(request, error)
    return redirect("checklists:template", template_id=template.id)


@login_required
@role_required(Role.MANAGER)
@require_POST
def item_add(request: HttpRequest, template_id: UUID) -> HttpResponse:
    template, _item = _template_for_item(request, template_id)
    form = ItemForm(request.POST)
    if not form.is_valid():
        return _items_response(request, template, _("Give the item a name."))
    services.add_item(
        actor=request.membership,
        template=template,
        label=form.cleaned_data["label"].strip(),
        photo_required=form.cleaned_data["photo_required"],
        skippable=not form.cleaned_data["must_do"],
    )
    return _items_response(request, template)


@login_required
@role_required(Role.MANAGER)
@require_POST
def item_move(request: HttpRequest, template_id: UUID, item_id: UUID) -> HttpResponse:
    template, item = _template_for_item(request, template_id, item_id)
    direction = "up" if request.POST.get("direction") == "up" else "down"
    services.move_item(actor=request.membership, item=item, direction=direction)
    return _items_response(request, template)


@login_required
@role_required(Role.MANAGER)
@require_POST
def item_remove(request: HttpRequest, template_id: UUID, item_id: UUID) -> HttpResponse:
    template, item = _template_for_item(request, template_id, item_id)
    services.remove_item(actor=request.membership, item=item)
    return _items_response(request, template)


@login_required
@role_required(Role.MANAGER)
@require_POST
def rule_add(request: HttpRequest, template_id: UUID) -> HttpResponse:
    _require_manage(request)
    template = get_object_or_404(ChecklistTemplate.objects.select_related("shift"), pk=template_id)
    form = RuleForm(request.POST, template=template)
    if not form.is_valid():
        form_error = " ".join(e for errors in form.errors.values() for e in errors)
        messages.error(request, form_error)
        return redirect("checklists:template", template_id=template.id)
    created = services.add_rule(
        actor=request.membership, template=template, fields=form.cleaned_data
    )
    messages.success(
        request,
        _("Schedule added — %(n)d checklist(s) scheduled for the next 48 hours.") % {"n": created},
    )
    return redirect("checklists:template", template_id=template.id)


@login_required
@role_required(Role.MANAGER)
@require_POST
def rule_toggle(request: HttpRequest, rule_id: UUID) -> HttpResponse:
    _require_manage(request)
    rule = get_object_or_404(RecurrenceRule.objects.select_related("template"), pk=rule_id)
    services.set_rule_active(actor=request.membership, rule=rule, active=not rule.is_active)
    return redirect("checklists:template", template_id=rule.template_id)


# --- runs ---


def _get_visible_run(request: HttpRequest, run_id: UUID) -> ChecklistRun:
    run = get_object_or_404(
        ChecklistRun.objects.select_related("location", "shift", "rule"), pk=run_id
    )
    if not ChecklistRun.objects.visible_to(request.membership).filter(pk=run.pk).exists():
        raise PermissionDenied
    return run


@login_required
@role_required(Role.SUPERVISOR)
def run_list(request: HttpRequest) -> HttpResponse:
    """Today's (or another day's) runs for the locations a supervisor sees."""
    try:
        day = date.fromisoformat(request.GET.get("day", ""))
    except ValueError:
        day = timezone.localdate()
    start = datetime.combine(day, time.min, tzinfo=timezone.get_current_timezone())
    day_runs = (
        ChecklistRun.objects.visible_to(request.membership)
        .with_progress()
        .annotate_overdue()
        .filter(due_at__gte=start, due_at__lt=start + timedelta(days=1))
        .select_related("location", "shift")
        .order_by("due_at")
    )
    return render(
        request,
        "checklists/run_list.html",
        {
            "runs": day_runs,
            "day": day,
            "previous_day": day - timedelta(days=1),
            "next_day": day + timedelta(days=1),
            "can_manage": can(request.membership, "template.manage"),
        },
    )


def _items_with_state(run: ChecklistRun) -> list[ChecklistRunItem]:
    items = list(ChecklistRunItem.objects.filter(run=run).order_by("order"))
    first_tick = {}
    for tick in (
        ChecklistRunItemTick.objects.filter(run_item__run=run)
        .select_related("membership__user")
        .order_by("trusted_time")
    ):
        first_tick.setdefault(tick.run_item_id, tick)
    for item in items:
        item.tick = first_tick.get(item.id)
    return items


def _run_context(request: HttpRequest, run: ChecklistRun, error: str | None = None) -> dict:
    items = _items_with_state(run)
    resolved = sum(1 for item in items if item.tick)
    return {
        "run": run,
        "items": items,
        "resolved": resolved,
        "percent": round(100 * resolved / len(items)) if items else 0,
        "actions": runs.run_actions(run, request.membership),
        "not_yet_open": timezone.now() < run.available_from,
        "flag_reasons": FLAG_REASONS,
        "error": error,
    }


def _panel(request: HttpRequest, run: ChecklistRun, error: str | None = None) -> HttpResponse:
    run.refresh_from_db()
    if _is_htmx(request):
        return render(request, "checklists/_run_panel.html", _run_context(request, run, error))
    if error:
        messages.error(request, error)
    return redirect("checklists:run", run_id=run.id)


def _fail_or_panel(request: HttpRequest, run: ChecklistRun, result) -> HttpResponse:
    if result.code == "forbidden":
        raise PermissionDenied
    if result.code == "not_found":
        raise Http404
    error = None if result.ok else result.message
    return _panel(request, run, error)


@login_required
def run_detail(request: HttpRequest, run_id: UUID) -> HttpResponse:
    run = _get_visible_run(request, run_id)
    return render(
        request,
        "checklists/run_detail.html",
        {**_run_context(request, run), **comments_context(request, run)},
    )


@login_required
@require_POST
def item_tick(request: HttpRequest, item_id: UUID) -> HttpResponse:
    item = get_object_or_404(ChecklistRunItem, pk=item_id)
    run = _get_visible_run(request, item.run_id)
    if not can(request.membership, "run.tick", obj=run):
        raise PermissionDenied

    photo = None
    upload = request.FILES.get("photo")
    if upload is not None:
        try:
            # Unlinked until the tick claims it (checklists.runs).
            photo = store_task_photo(
                uploaded_by=request.membership, upload=upload, checklist_run=run, link=False
            )
        except PhotoError as exc:
            return _panel(request, run, str(exc))
    return _fail_or_panel(request, run, runs.tick_item(item, request.membership, photo=photo))


@login_required
@require_POST
def item_skip(request: HttpRequest, item_id: UUID) -> HttpResponse:
    item = get_object_or_404(ChecklistRunItem, pk=item_id)
    run = _get_visible_run(request, item.run_id)
    result = runs.skip_item(item, request.membership, reason=request.POST.get("reason", ""))
    return _fail_or_panel(request, run, result)


@login_required
@require_POST
def run_flag(request: HttpRequest, run_id: UUID) -> HttpResponse:
    run = _get_visible_run(request, run_id)
    result = runs.flag_run(
        run,
        request.membership,
        reason=request.POST.get("reason", ""),
        note=request.POST.get("note", ""),
    )
    return _fail_or_panel(request, run, result)


@login_required
@require_POST
def run_resolve_flag(request: HttpRequest, run_id: UUID) -> HttpResponse:
    run = _get_visible_run(request, run_id)
    result = runs.resolve_run_flag(run, request.membership, note=request.POST.get("note", ""))
    return _fail_or_panel(request, run, result)


@login_required
@require_POST
def run_cancel(request: HttpRequest, run_id: UUID) -> HttpResponse:
    run = _get_visible_run(request, run_id)
    return _fail_or_panel(request, run, runs.cancel_run(run, request.membership))


@login_required
@require_POST
def run_comment_add(request: HttpRequest, run_id: UUID) -> HttpResponse:
    run = _get_visible_run(request, run_id)
    if not can(request.membership, "comment.add", obj=run):
        raise PermissionDenied
    error = None
    try:
        task_services.add_comment(
            actor=request.membership, checklist_run=run, body=request.POST.get("body", "")
        )
    except task_services.CommentError as exc:
        error = str(exc)
    if _is_htmx(request):
        return render(request, "tasks/_comments.html", comments_context(request, run, error))
    return redirect("checklists:run", run_id=run.id)


@login_required
@require_POST
def run_comment_delete(request: HttpRequest, run_id: UUID, comment_id: UUID) -> HttpResponse:
    run = _get_visible_run(request, run_id)
    comment = get_object_or_404(
        TaskComment, pk=comment_id, checklist_run=run, deleted_at__isnull=True
    )
    task_services.delete_comment(actor=request.membership, comment=comment)
    if _is_htmx(request):
        return render(request, "tasks/_comments.html", comments_context(request, run))
    return redirect("checklists:run", run_id=run.id)
