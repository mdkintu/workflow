"""The task state machine (docs/04-design.md §5.1-5.2, ADR-17). This is the
only code that changes Task.status (CLAUDE.md "Task status"): HTMX views call
apply() now, and the sync mutation handlers will call the same function
(Feature 6).

Stored statuses: pending → in_progress → done | flagged | cancelled.
Overdue is derived (Task.objects.annotate_overdue()), never stored here.

Implemented: T1-T9. Changes arriving by sync (offline=True) may also
complete a task that is already done (the second completion is kept as
evidence, `already_done`) or was cancelled meanwhile (T9: done if it was
completed before the cancel, otherwise flagged for review).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from notifications.services import notify_flag, notify_rejected
from organisations.models import AuditEvent, Membership
from organisations.permissions import can
from organisations.tenancy import tenant_context
from tasks.models import FlagKind, Task, TaskComment, TaskPhoto, TaskStatus


class Action(StrEnum):
    START = "start"
    COMPLETE = "complete"
    FLAG = "flag"
    REJECT = "reject"
    RESOLVE_FLAG = "resolve_flag"
    CANCEL = "cancel"


# Action -> permission key in organisations.permissions (docs/04-design.md §2).
PERMISSION = {
    Action.START: "task.start",
    Action.COMPLETE: "task.complete",
    Action.FLAG: "task.flag",
    Action.REJECT: "task.reject",
    Action.RESOLVE_FLAG: "task.resolve_flag",
    Action.CANCEL: "task.cancel",
}

# Action -> statuses it may start from (docs/04-design.md §5.2).
_FROM = {
    Action.START: {TaskStatus.PENDING},
    Action.COMPLETE: {TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.FLAGGED},
    Action.FLAG: {TaskStatus.PENDING, TaskStatus.IN_PROGRESS},
    Action.REJECT: {TaskStatus.DONE},
    Action.RESOLVE_FLAG: {TaskStatus.FLAGGED},
    Action.CANCEL: {TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.FLAGGED},
}


@dataclass(frozen=True)
class Result:
    """`code` is a docs/04-design.md §4.5 reason code when `ok` is False."""

    ok: bool
    code: str | None = None
    message: str = ""


_OK = Result(ok=True)


def _fail(code: str, message: str) -> Result:
    return Result(ok=False, code=code, message=message)


def _state_allows(task: Task, action: Action, *, offline: bool = False) -> bool:
    if (
        offline
        and action == Action.COMPLETE
        and task.status
        in (
            TaskStatus.DONE,
            TaskStatus.CANCELLED,
        )
    ):
        return True  # decided in _complete (already_done / T9)
    if task.status not in _FROM[action]:
        return False
    # T5: only a problem raised by staff can be "completed anyway"; a
    # rejection (or a sync conflict) needs a supervisor to resolve it first.
    if action == Action.COMPLETE and task.status == TaskStatus.FLAGGED:
        return task.flag_kind == FlagKind.PROBLEM
    return True


def _is_visible(task: Task, membership: Membership) -> bool:
    return Task.objects.visible_to(membership).filter(pk=task.pk).exists()


def allowed_actions(
    task: Task, membership: Membership | None, *, known_visible: bool = False
) -> list[Action]:
    """The actions `membership` may take on `task` right now — drives which
    buttons the UI shows. apply() re-checks everything regardless.
    `known_visible=True` skips the visibility query when the caller loaded
    `task` through `Task.objects.visible_to(membership)` (the task board)."""
    if membership is None or task.organisation_id != membership.organisation_id:
        return []
    if not known_visible:
        with tenant_context(membership.organisation):
            if not _is_visible(task, membership):
                return []
    return [
        action
        for action in Action
        if can(membership, PERMISSION[action], obj=task) and _state_allows(task, action)
    ]


def apply(
    task: Task,
    action: Action | str,
    actor: Membership,
    *,
    reason: str = "",
    note: str = "",
    device_time: datetime | None = None,
    trusted_time: datetime | None = None,
    time_untrusted: bool = False,
    offline: bool = False,
    photo_id=None,
    comment_id=None,
) -> Result:
    """Applies one transition, or explains why not. On success `task` is
    refreshed in place. Runs in its own tenant_context and transaction, with
    the row locked, so concurrent actions on one task serialise.

    From the sync push: `offline`, the phone's `device_time` and the
    server's `trusted_time` for it (docs/02-architecture.md §4.6), and the
    ids the phone generated for the photo or flag comment it refers to."""
    action = Action(action)
    if actor is None or task.organisation_id != actor.organisation_id:
        return _fail("not_found", _("This task was not found."))

    with tenant_context(actor.organisation), transaction.atomic():
        locked = Task.objects.select_for_update().get(pk=task.pk)
        if not can(actor, PERMISSION[action], obj=locked) or not _is_visible(locked, actor):
            return _fail("forbidden", _("You can't do that."))
        if not _state_allows(locked, action, offline=offline):
            return _fail("invalid_transition", _("This task has already moved on."))

        old_status = locked.status
        result = _HANDLERS[action](
            locked,
            actor,
            reason=reason.strip(),
            note=note.strip(),
            now=timezone.now(),
            device_time=device_time,
            trusted_time=trusted_time,
            time_untrusted=time_untrusted,
            photo_id=photo_id,
            comment_id=comment_id,
        )
        if not result.ok:
            return result

        AuditEvent.objects.create(
            actor=actor,
            action=f"task.{action.value}",
            target_type="task",
            target_id=locked.id,
            changes={"status": [old_status, locked.status]},
        )

    task.refresh_from_db()
    return result


# --- handlers: validate their own inputs, mutate `task`, save ---


def _start(task, actor, *, now, **_kw) -> Result:
    task.status = TaskStatus.IN_PROGRESS
    task.started_by = actor
    task.save(update_fields=["status", "started_by", "updated_at"])
    return _OK


def _complete(
    task, actor, *, now, device_time, trusted_time, time_untrusted, photo_id, **_kw
) -> Result:
    when = trusted_time or device_time or now
    photo = None
    if photo_id is not None:
        photo = TaskPhoto.objects.filter(pk=photo_id, task=task).first()
        if photo is None:
            # The phone uploads photos before the changes that use them; this
            # one hasn't arrived yet, so the phone keeps the change and retries.
            return _fail("photo_not_uploaded", _("The photo is still uploading."))

    if task.status == TaskStatus.DONE:
        # Two people finished it offline: the first stays the completion, this
        # one is kept as evidence (docs/02-architecture.md §4.5).
        _comment(
            task,
            actor,
            _("Also completed by %(name)s") % {"name": actor.name},
            is_flag=False,
            when=when,
        )
        return Result(ok=True, code="already_done", message=_("Someone else finished this first."))

    has_photo = photo is not None or task.photos.filter(linked_at__isnull=False).exists()
    if task.photo_required and not has_photo:
        return _fail("photo_required", _("Add a photo before marking this done."))

    fields = [
        "status",
        "completed_by",
        "completed_at_device",
        "completed_at_trusted",
        "completed_received_at",
        "due_at_when_completed",
        "time_untrusted",
        "updated_at",
    ]
    code = None
    if task.status == TaskStatus.CANCELLED:
        # T9: a completion that reached the server after a cancel.
        if task.cancelled_at is not None and when > task.cancelled_at:
            task.status = TaskStatus.FLAGGED
            task.flag_kind = FlagKind.COMPLETED_AFTER_CANCEL
            task.flag_reason = str(_("Completed after it was cancelled"))
            fields += ["flag_kind", "flag_reason"]
            code = "task_cancelled_flagged"
        else:
            task.status = TaskStatus.DONE
    else:
        if task.status == TaskStatus.FLAGGED:  # T5: completed despite a problem
            task.flag_kind = None
            task.flag_reason = ""
            task.flag_resolved_note = _("Completed by %(name)s") % {"name": actor.name}
            fields += ["flag_kind", "flag_reason", "flag_resolved_note"]
        task.status = TaskStatus.DONE

    task.completed_by = actor
    task.completed_at_device = device_time or now
    task.completed_at_trusted = when
    task.completed_received_at = now
    task.due_at_when_completed = task.due_at
    task.time_untrusted = time_untrusted
    task.save(update_fields=fields)
    if code is None and time_untrusted:
        code = "time_untrusted"
    return Result(ok=True, code=code)


def _flag(task, actor, *, reason, note, now, device_time, comment_id=None, **_kw) -> Result:
    if not reason:
        return _fail("validation_error", _("Say what the problem is."))
    task.status = TaskStatus.FLAGGED
    task.flag_kind = FlagKind.PROBLEM
    task.flag_reason = reason[:300]
    task.flagged_by = actor
    task.save(update_fields=["status", "flag_kind", "flag_reason", "flagged_by", "updated_at"])
    body = f"{reason}: {note}" if note else reason
    _comment(task, actor, body, is_flag=True, when=device_time or now, comment_id=comment_id)
    # F5.3: alert the supervisor on duty (queued; notifications.dispatch sends it).
    notify_flag(
        location=task.location,
        target_type="task",
        target_id=task.id,
        title=task.title,
        raised_by=actor,
        reason=reason,
        now=now,
    )
    return _OK


def _reject(task, actor, *, reason, now, **_kw) -> Result:
    if not reason:
        return _fail("validation_error", _("Say why the work is being sent back."))
    # Completion fields are kept: they're the history of what was done.
    task.status = TaskStatus.FLAGGED
    task.flag_kind = FlagKind.REJECTED
    task.flag_reason = reason[:300]
    task.flagged_by = actor
    task.save(update_fields=["status", "flag_kind", "flag_reason", "flagged_by", "updated_at"])
    _comment(task, actor, _("Sent back: %(reason)s") % {"reason": reason}, is_flag=True, when=now)
    notify_rejected(task=task, rejected_by=actor, reason=reason, now=now)  # F5.3
    return _OK


def _resolve_flag(task, actor, *, note, **_kw) -> Result:
    task.status = TaskStatus.IN_PROGRESS
    task.flag_kind = None
    task.flag_reason = ""
    task.flag_resolved_note = note[:300]
    task.save(
        update_fields=["status", "flag_kind", "flag_reason", "flag_resolved_note", "updated_at"]
    )
    return _OK


def _cancel(task, actor, *, now, **_kw) -> Result:
    task.status = TaskStatus.CANCELLED
    task.cancelled_by = actor
    task.cancelled_at = now
    task.save(update_fields=["status", "cancelled_by", "cancelled_at", "updated_at"])
    return _OK


def _comment(
    task: Task, author: Membership, body: str, *, is_flag: bool, when, comment_id=None
) -> None:
    extra = {"id": comment_id} if comment_id is not None else {}
    TaskComment.objects.create(
        task=task, author=author, body=body[:500], is_flag=is_flag, device_time=when, **extra
    )


_HANDLERS = {
    Action.START: _start,
    Action.COMPLETE: _complete,
    Action.FLAG: _flag,
    Action.REJECT: _reject,
    Action.RESOLVE_FLAG: _resolve_flag,
    Action.CANCEL: _cancel,
}
