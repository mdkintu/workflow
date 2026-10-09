"""Checklist runs: ticking and skipping items, and the run state machine
(docs/04-design.md §5.4, docs/01-requirements.md F2.3). This is the only
code that changes ChecklistRun.status apart from the generator (creation)
and a paused schedule (system cancel, checklists.services).

    pending → in_progress         first tick or skip
    in_progress → done            every item resolved, none skipped (automatic)
    in_progress → flagged         every item resolved with ≥ 1 skipped, or a
                                  problem reported (flag comment) at any time
    flagged → done | in_progress  a Supervisor+ resolves it (done if every
                                  item is resolved, otherwise back to work)
    pending, in_progress → cancelled   Supervisor+

Ticks are append-only. The effective tick for an item is the earliest; a
second tick online is reported as `already_done` and not stored (two ticks
made offline are both kept — that's the sync handler's job).
"""

from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from checklists.models import (
    ChecklistRun,
    ChecklistRunItem,
    ChecklistRunItemTick,
    ChecklistRunStatus,
)
from notifications.services import notify_flag
from organisations.models import AuditEvent, Membership
from organisations.permissions import can
from organisations.tenancy import tenant_context
from tasks.models import TaskComment, TaskPhoto
from tasks.transitions import Result

TICKABLE = {ChecklistRunStatus.PENDING, ChecklistRunStatus.IN_PROGRESS, ChecklistRunStatus.FLAGGED}
FLAGGABLE = {ChecklistRunStatus.PENDING, ChecklistRunStatus.IN_PROGRESS}
CANCELLABLE = {ChecklistRunStatus.PENDING, ChecklistRunStatus.IN_PROGRESS}

_OK = Result(ok=True)


def _fail(code: str, message: str) -> Result:
    return Result(ok=False, code=code, message=message)


def _is_visible(run: ChecklistRun, membership: Membership) -> bool:
    return ChecklistRun.objects.visible_to(membership).filter(pk=run.pk).exists()


def run_actions(run: ChecklistRun, membership: Membership | None) -> list[str]:
    """What `membership` may do on `run` now — drives the UI's buttons."""
    if membership is None or run.organisation_id != membership.organisation_id:
        return []
    with tenant_context(membership.organisation):
        if not _is_visible(run, membership):
            return []
    actions = []
    if run.status in TICKABLE:
        actions += [a for a in ("tick", "skip") if can(membership, f"run.{a}", obj=run)]
    if run.status in FLAGGABLE and can(membership, "run.flag", obj=run):
        actions.append("flag")
    if run.status == ChecklistRunStatus.FLAGGED and can(membership, "run.resolve_flag", obj=run):
        actions.append("resolve_flag")
    if run.status in CANCELLABLE and can(membership, "run.cancel", obj=run):
        actions.append("cancel")
    return actions


def _lock(run_id, actor: Membership, permission: str) -> tuple[ChecklistRun | None, Result | None]:
    """Inside tenant_context + transaction: lock the run and check the
    actor may do `permission` on it."""
    run = ChecklistRun.objects.select_for_update(of=("self",)).select_related("rule").get(pk=run_id)
    if not can(actor, permission, obj=run) or not _is_visible(run, actor):
        return None, _fail("forbidden", _("You can't do that."))
    return run, None


def _recompute(run: ChecklistRun, now: datetime) -> None:
    """Moves the run along after a tick or skip (automatic transitions)."""
    effective: dict = {}
    for tick in ChecklistRunItemTick.objects.filter(run_item__run=run).order_by("trusted_time"):
        effective.setdefault(tick.run_item_id, tick.skipped)
    item_count = ChecklistRunItem.objects.filter(run=run).count()

    status = run.status
    if status == ChecklistRunStatus.PENDING and effective:
        status = ChecklistRunStatus.IN_PROGRESS
    if status == ChecklistRunStatus.IN_PROGRESS and len(effective) == item_count:
        skipped_any = any(effective.values())
        status = ChecklistRunStatus.FLAGGED if skipped_any else ChecklistRunStatus.DONE
        run.completed_at_trusted = now
    if status != run.status:
        run.status = status
        run.save(update_fields=["status", "completed_at_trusted", "updated_at"])


def _resolve_item(
    item: ChecklistRunItem,
    actor: Membership,
    *,
    skip: bool,
    reason: str = "",
    photo: TaskPhoto | None = None,
    device_time: datetime | None = None,
    trusted_time: datetime | None = None,
    tick_id=None,
    offline: bool = False,
) -> Result:
    if actor is None or item.organisation_id != actor.organisation_id:
        return _fail("not_found", _("This checklist was not found."))
    reason = reason.strip()

    with tenant_context(actor.organisation), transaction.atomic():
        run, failure = _lock(item.run_id, actor, "run.skip" if skip else "run.tick")
        if failure:
            return failure
        if run.status not in TICKABLE:
            return _fail("invalid_transition", _("This checklist is already finished."))
        now = timezone.now()
        if now < run.available_from:
            return _fail("not_yet_available", _("This checklist isn't open yet."))

        item = ChecklistRunItem.objects.get(pk=item.pk)
        already = item.ticks.exists()
        if already and not offline:
            return Result(ok=True, code="already_done", message=_("Someone already did this."))
        if skip:
            if not item.skippable:
                return _fail("not_skippable", _("This item can't be skipped."))
            if not reason:
                return _fail("validation_error", _("Say why you're skipping it."))
        elif item.photo_required and photo is None:
            return _fail("photo_required", _("Add a photo for this item."))
        if photo is not None and photo.checklist_run_id != run.id:
            return _fail("validation_error", _("That photo belongs to something else."))

        # Two offline ticks on one item are both kept; the earliest counts.
        extra = {"id": tick_id} if tick_id is not None else {}
        ChecklistRunItemTick.objects.create(
            run_item=item,
            membership=actor,
            skipped=skip,
            skip_reason=reason[:200],
            photo=photo,
            device_time=device_time or now,
            trusted_time=trusted_time or device_time or now,
            **extra,
        )
        if photo is not None and photo.linked_at is None:
            photo.linked_at = now
            photo.save(update_fields=["linked_at", "updated_at"])
        _recompute(run, now)
    if already:
        return Result(ok=True, code="already_done", message=_("Someone already did this."))
    return _OK


def tick_item(
    item: ChecklistRunItem, actor: Membership, *, photo: TaskPhoto | None = None, **kw
) -> Result:
    """kw: device_time, trusted_time, tick_id, offline (from the sync push)."""
    return _resolve_item(item, actor, skip=False, photo=photo, **kw)


def skip_item(item: ChecklistRunItem, actor: Membership, *, reason: str, **kw) -> Result:
    return _resolve_item(item, actor, skip=True, reason=reason, **kw)


def _run_action(
    run: ChecklistRun, actor: Membership, permission: str, allowed: set, apply
) -> Result:
    if actor is None or run.organisation_id != actor.organisation_id:
        return _fail("not_found", _("This checklist was not found."))
    with tenant_context(actor.organisation), transaction.atomic():
        locked, failure = _lock(run.pk, actor, permission)
        if failure:
            return failure
        if locked.status not in allowed:
            return _fail("invalid_transition", _("This checklist has already moved on."))
        old = locked.status
        result = apply(locked, timezone.now())
        if not result.ok:
            return result
        AuditEvent.objects.create(
            actor=actor,
            action=permission,
            target_type="checklistrun",
            target_id=locked.id,
            changes={"status": [old, locked.status]},
        )
    run.refresh_from_db()
    return result


def flag_run(
    run: ChecklistRun, actor: Membership, *, reason: str, note: str = "", comment_id=None
) -> Result:
    reason, note = reason.strip(), note.strip()

    def apply(locked, now):
        if not reason:
            return _fail("validation_error", _("Say what the problem is."))
        locked.status = ChecklistRunStatus.FLAGGED
        locked.save(update_fields=["status", "updated_at"])
        extra = {"id": comment_id} if comment_id is not None else {}
        TaskComment.objects.create(
            checklist_run=locked,
            author=actor,
            body=(f"{reason}: {note}" if note else reason)[:500],
            is_flag=True,
            device_time=now,
            **extra,
        )
        notify_flag(  # F5.3
            location=locked.location,
            target_type="run",
            target_id=locked.id,
            title=locked.name,
            raised_by=actor,
            reason=reason,
            now=now,
        )
        return _OK

    return _run_action(run, actor, "run.flag", FLAGGABLE, apply)


def resolve_run_flag(run: ChecklistRun, actor: Membership, *, note: str = "") -> Result:
    note = note.strip()

    def apply(locked, now):
        items = ChecklistRunItem.objects.filter(run=locked)
        unresolved = items.filter(ticks__isnull=True).exists()
        if unresolved:
            locked.status = ChecklistRunStatus.IN_PROGRESS
        else:
            locked.status = ChecklistRunStatus.DONE
            locked.completed_at_trusted = locked.completed_at_trusted or now
        locked.save(update_fields=["status", "completed_at_trusted", "updated_at"])
        if note:
            TaskComment.objects.create(
                checklist_run=locked,
                author=actor,
                body=_("Resolved: %(note)s") % {"note": note},
                device_time=now,
            )
        return _OK

    return _run_action(run, actor, "run.resolve_flag", {ChecklistRunStatus.FLAGGED}, apply)


def cancel_run(run: ChecklistRun, actor: Membership) -> Result:
    def apply(locked, now):
        locked.status = ChecklistRunStatus.CANCELLED
        locked.save(update_fields=["status", "updated_at"])
        return _OK

    return _run_action(run, actor, "run.cancel", CANCELLABLE, apply)
