"""Task create/edit/reassign and comments. Views stay thin (CLAUDE.md "Code
style"); status changes are not here — only tasks/transitions.py does those.
Each function opens its own tenant_context from the acting membership, so it
is safe to call outside a request too.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _

from organisations.models import AuditEvent, Membership
from organisations.tenancy import tenant_context
from tasks.models import Task, TaskComment, TaskStatus

# The fields a create/edit form may set. Status is deliberately absent.
EDITABLE_FIELDS = (
    "title",
    "description",
    "location",
    "assignee_membership",
    "assignee_shift",
    "assignee_location",
    "shift_date",
    "due_at",
    "photo_required",
    "reminder_lead_min",
)
EDITABLE_STATUSES = {TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.FLAGGED}
COMMENT_MAX_LENGTH = 500
COMMENT_DELETE_WINDOW = timedelta(minutes=5)


class TaskNotEditable(Exception):
    """Done and cancelled tasks are history; they aren't edited or reassigned."""


class CommentError(Exception):
    """A user-facing reason a comment can't be saved."""


def _audit_value(value: Any) -> Any:
    if isinstance(value, models.Model):
        return str(value.pk)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def create_task(*, actor: Membership, fields: dict[str, Any]) -> Task:
    with tenant_context(actor.organisation):
        task = Task.objects.create(
            created_by=actor, **{name: fields[name] for name in EDITABLE_FIELDS}
        )
        AuditEvent.objects.create(
            actor=actor,
            action="task.create",
            target_type="task",
            target_id=task.id,
            changes={name: [None, _audit_value(fields[name])] for name in EDITABLE_FIELDS},
        )
    return task


def update_task(*, actor: Membership, task: Task, fields: dict[str, Any]) -> Task:
    """Edit and reassign are the same operation: any of EDITABLE_FIELDS,
    recorded field by field in the audit log (docs/01-requirements.md
    NFR-S11)."""
    if task.status not in EDITABLE_STATUSES:
        raise TaskNotEditable

    changes = {}
    for name in EDITABLE_FIELDS:
        old, new = getattr(task, name), fields[name]
        if old != new:
            changes[name] = [_audit_value(old), _audit_value(new)]
            setattr(task, name, new)
    if not changes:
        return task

    with tenant_context(actor.organisation):
        task.save(update_fields=[*changes, "updated_at"])
        AuditEvent.objects.create(
            actor=actor,
            action="task.edit",
            target_type="task",
            target_id=task.id,
            changes=changes,
        )
    return task


def add_comment(
    *,
    actor: Membership,
    body: str,
    task: Task | None = None,
    checklist_run=None,
    comment_id=None,
    device_time=None,
) -> TaskComment:
    """A comment on a task or a checklist run (exactly one — the database
    enforces it too)."""
    body = body.strip()
    if not body:
        raise CommentError(_("Write something first."))
    if len(body) > COMMENT_MAX_LENGTH:
        raise CommentError(_("Keep it under %(n)d characters.") % {"n": COMMENT_MAX_LENGTH})
    with tenant_context(actor.organisation):
        return TaskComment.objects.create(
            task=task,
            checklist_run=checklist_run,
            author=actor,
            body=body,
            device_time=device_time or timezone.now(),
            **({"id": comment_id} if comment_id is not None else {}),
        )


def delete_comment(*, actor: Membership, comment: TaskComment) -> None:
    """Authors may take a comment back within five minutes; after that it's
    part of the record (docs/01-requirements.md F4.1)."""
    too_late = timezone.now() - comment.created_at > COMMENT_DELETE_WINDOW
    if comment.author_id != actor.id or too_late:
        raise PermissionDenied
    comment.deleted_at = timezone.now()
    with tenant_context(actor.organisation):
        comment.save(update_fields=["deleted_at", "updated_at"])


def visible_comments(parent):
    """Comments on a task or a checklist run (both have `.comments`)."""
    return (
        parent.comments.filter(deleted_at__isnull=True)
        .select_related("author__user")
        .order_by("device_time", "created_at")
    )


BOARD_COLUMNS = (
    (TaskStatus.PENDING, "dot-idle"),
    (TaskStatus.IN_PROGRESS, "dot-info"),
    (TaskStatus.FLAGGED, "dot-warning"),
    (TaskStatus.DONE, "dot-success"),
)


def board_columns(tasks: list[Task], membership: Membership) -> list[dict[str, Any]]:
    """The task board (F1.6, ADR-21): tasks grouped by status, each with the
    one forward move the member may make from the board (`next_action`, an
    Action value) and whether "Send back" applies. `tasks` must come from
    `Task.objects.visible_to(membership)`; nothing here changes a status."""
    from tasks.transitions import Action, allowed_actions

    columns = {status: [] for status, _dot in BOARD_COLUMNS}
    for task in tasks:
        if task.status not in columns:
            continue  # cancelled work isn't on the board
        actions = allowed_actions(task, membership, known_visible=True)
        task.next_action = None
        task.needs_photo = False
        if task.status == TaskStatus.PENDING and Action.START in actions:
            task.next_action = Action.START
        elif task.status == TaskStatus.IN_PROGRESS and Action.COMPLETE in actions:
            if task.photo_required:
                task.needs_photo = True  # the photo is added on the task page
            else:
                task.next_action = Action.COMPLETE
        elif task.status == TaskStatus.FLAGGED and Action.RESOLVE_FLAG in actions:
            task.next_action = Action.RESOLVE_FLAG
        task.can_send_back = Action.REJECT in actions
        columns[task.status].append(task)
    return [
        {"status": status, "label": TaskStatus(status).label, "dot": dot, "tasks": columns[status]}
        for status, dot in BOARD_COLUMNS
    ]
