"""POST /api/sync/push (docs/04-design.md §4.5, ADR-04).

A batch of offline changes ("mutations"), each applied in its own savepoint
and in order; one rejection doesn't stop the rest. Every change goes through
the same code as the online screens (tasks.transitions, checklists.runs,
tasks.services), so the conflict rules live in one place:

    status only moves forward           invalid_transition otherwise
    completed after a cancel            T9: done, or flagged for review
    completed twice                     the first stands; already_done
    comments and ticks                  append-only

Idempotency: `mutation_id` (generated on the phone) is the primary key of
OfflineSyncLog, written in the same savepoint as the change, so a change is
never applied without being logged or logged without being applied. A
replay returns the stored result as `duplicate`. The log doubles as the
server-side audit trail of what arrived from which device and when.

`retry` (a photo the phone hasn't uploaded yet) is the one outcome that is
not logged: the phone keeps the change and sends it again later.

Trusted time (docs/02-architecture.md §4.6): the phone sends device times
already corrected by its last known clock offset; the server trusts them
unless they're in the future or before the thing they refer to existed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _

from checklists import runs as run_actions
from checklists.models import ChecklistRun, ChecklistRunItem
from organisations.models import Membership
from organisations.permissions import can
from sync.models import OfflineSyncLog, SyncResultStatus
from tasks import services as task_services
from tasks.models import Task, TaskComment, TaskPhoto
from tasks.transitions import Action, Result, apply

MAX_MUTATIONS = 100
FUTURE_TOLERANCE = timedelta(minutes=2)
PAST_TOLERANCE = timedelta(minutes=5)


class BadEnvelope(Exception):
    """The request body isn't a valid push (→ 400 for the whole request)."""


@dataclass(frozen=True)
class Context:
    membership: Membership
    received_at: datetime
    device_time: datetime


class Rejected(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(code)
        self.code, self.message = code, message


def _uuid(payload: dict, key: str, *, required: bool = True) -> UUID | None:
    value = payload.get(key)
    if value in (None, "") and not required:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise Rejected("validation_error", f"{key} must be a UUID") from exc


def _trust(ctx: Context, created: datetime | None) -> tuple[datetime, bool]:
    """(trusted time, untrusted?) for the device time of this change."""
    if ctx.device_time > ctx.received_at + FUTURE_TOLERANCE:
        return ctx.received_at, True
    if created is not None and ctx.device_time < created - PAST_TOLERANCE:
        return ctx.received_at, True
    return ctx.device_time, False


def _visible_task(ctx: Context, task_id: UUID) -> Task:
    task = Task.objects.visible_to(ctx.membership).filter(pk=task_id).first()
    if task is None:  # gone, or not theirs any more
        raise Rejected("not_found", _("This task was removed by your manager."))
    return task


def _visible_run(ctx: Context, run_id: UUID) -> ChecklistRun:
    run = ChecklistRun.objects.visible_to(ctx.membership).filter(pk=run_id).first()
    if run is None:
        raise Rejected("not_found", _("This checklist was removed by your manager."))
    return run


def _task_result(result: Result, task: Task) -> dict:
    if result.ok:
        task.refresh_from_db()
        return {
            "status": "applied",
            "code": result.code,
            "message": result.message,
            "record": {
                "type": "tasks",
                "id": str(task.id),
                "status": task.status,
                "updated_seq": task.updated_seq,
            },
        }
    if result.code == "photo_not_uploaded":
        return {"status": "retry", "code": result.code, "message": result.message}
    raise Rejected(result.code, result.message)


def _task_action(action: Action) -> Callable:
    def handler(ctx: Context, payload: dict) -> dict:
        task = _visible_task(ctx, _uuid(payload, "task_id"))
        trusted, untrusted = _trust(ctx, task.created_at)
        result = apply(
            task,
            action,
            ctx.membership,
            reason=str(payload.get("reason", "")),
            note=str(payload.get("note", "")),
            device_time=ctx.device_time,
            trusted_time=trusted,
            time_untrusted=untrusted,
            offline=True,
            photo_id=_uuid(payload, "photo_id", required=False),
            comment_id=_uuid(payload, "comment_id", required=action == Action.FLAG),
        )
        return _task_result(result, task)

    return handler


def _item_action(skip: bool) -> Callable:
    def handler(ctx: Context, payload: dict) -> dict:
        tick_id = _uuid(payload, "tick_id")
        item = ChecklistRunItem.objects.filter(pk=_uuid(payload, "run_item_id")).first()
        if item is None:
            raise Rejected("not_found", _("This checklist was removed by your manager."))
        run = _visible_run(ctx, item.run_id)
        trusted, _untrusted = _trust(ctx, run.created_at)
        common = {
            "device_time": ctx.device_time,
            "trusted_time": trusted,
            "tick_id": tick_id,
            "offline": True,
        }
        if skip:
            result = run_actions.skip_item(
                item, ctx.membership, reason=str(payload.get("reason", "")), **common
            )
        else:
            photo = None
            photo_id = _uuid(payload, "photo_id", required=False)
            if photo_id is not None:
                photo = TaskPhoto.objects.filter(pk=photo_id, checklist_run=run).first()
                if photo is None:
                    return {
                        "status": "retry",
                        "code": "photo_not_uploaded",
                        "message": _("The photo is still uploading."),
                    }
            result = run_actions.tick_item(item, ctx.membership, photo=photo, **common)
        if not result.ok:
            raise Rejected(result.code, result.message)
        run.refresh_from_db()
        return {
            "status": "applied",
            "code": result.code,
            "message": result.message,
            "record": {
                "type": "runs",
                "id": str(run.id),
                "status": run.status,
                "updated_seq": run.updated_seq,
            },
        }

    return handler


def _comment_add(ctx: Context, payload: dict) -> dict:
    comment_id = _uuid(payload, "comment_id")
    task_id = _uuid(payload, "task_id", required=False)
    run_id = _uuid(payload, "run_id", required=False)
    if (task_id is None) == (run_id is None):
        raise Rejected("validation_error", "exactly one of task_id and run_id")
    parent = _visible_task(ctx, task_id) if task_id else _visible_run(ctx, run_id)
    body = str(payload.get("body", ""))

    if run_id and payload.get("is_flag"):  # a problem reported on a checklist
        result = run_actions.flag_run(parent, ctx.membership, reason=body, comment_id=comment_id)
        if not result.ok:
            raise Rejected(result.code, result.message)
    else:
        if not can(ctx.membership, "comment.add", obj=parent):
            raise Rejected("forbidden", _("You can't do that."))
        trusted, _untrusted = _trust(ctx, parent.created_at)
        try:
            task_services.add_comment(
                actor=ctx.membership,
                body=body,
                task=parent if task_id else None,
                checklist_run=parent if run_id else None,
                comment_id=comment_id,
                device_time=trusted,
            )
        except task_services.CommentError as exc:
            raise Rejected("validation_error", str(exc)) from exc
    return {
        "status": "applied",
        "code": None,
        "message": "",
        "record": {"type": "comments", "id": str(comment_id)},
    }


def _comment_delete(ctx: Context, payload: dict) -> dict:
    comment = TaskComment.objects.filter(pk=_uuid(payload, "comment_id")).first()
    if comment is None:
        raise Rejected("not_found")
    try:
        task_services.delete_comment(actor=ctx.membership, comment=comment)
    except PermissionDenied as exc:
        raise Rejected(
            "forbidden", _("You can only delete your own comment, within 5 minutes.")
        ) from exc
    return {
        "status": "applied",
        "code": None,
        "message": "",
        "record": {"type": "comments", "id": str(comment.id)},
    }


HANDLERS: dict[str, Callable[[Context, dict], dict]] = {
    "task.start": _task_action(Action.START),
    "task.complete": _task_action(Action.COMPLETE),
    "task.flag": _task_action(Action.FLAG),
    "run.item_tick": _item_action(skip=False),
    "run.item_skip": _item_action(skip=True),
    "comment.add": _comment_add,
    "comment.delete": _comment_delete,
}


def parse(body: Any) -> tuple[UUID, list[dict]]:
    """Validates the envelope; raises BadEnvelope."""
    if not isinstance(body, dict) or not isinstance(body.get("mutations"), list):
        raise BadEnvelope("expected {device_id, mutations: [...]}")
    try:
        device_id = UUID(str(body.get("device_id")))
    except ValueError as exc:
        raise BadEnvelope("device_id must be a UUID") from exc
    parsed = []
    for m in body["mutations"]:
        try:
            mutation_id = UUID(str(m["mutation_id"]))
            device_time = parse_datetime(str(m["device_time"]))
            payload = m.get("payload") or {}
        except (KeyError, TypeError, ValueError) as exc:
            raise BadEnvelope("each mutation needs mutation_id, kind, device_time") from exc
        if device_time is None or device_time.tzinfo is None or not isinstance(payload, dict):
            raise BadEnvelope("device_time must be an ISO-8601 time with a zone")
        parsed.append(
            {
                "mutation_id": mutation_id,
                "kind": str(m.get("kind", "")),
                "device_time": device_time,
                "payload": payload,
            }
        )
    return device_id, parsed


def run_push(
    membership: Membership, device_id: UUID, mutations: list[dict], received_at: datetime
) -> list[dict]:
    """Runs inside the request's tenant context."""
    results = []
    for m in mutations:
        mutation_id = m["mutation_id"]
        logged = OfflineSyncLog.objects.filter(pk=mutation_id).first()
        if logged is not None:
            if logged.membership_id != membership.id:
                results.append(_rejected(mutation_id, "forbidden", ""))
            else:
                results.append({**logged.result_json, "status": "duplicate"})
            continue

        ctx = Context(membership=membership, received_at=received_at, device_time=m["device_time"])
        handler = HANDLERS.get(m["kind"])
        try:
            with transaction.atomic():
                try:
                    if handler is None:
                        raise Rejected("unknown_kind")
                    outcome = handler(ctx, m["payload"])
                except Rejected as exc:
                    outcome = {"status": "rejected", "code": exc.code, "message": exc.message}
                result = {"mutation_id": str(mutation_id), "record": None, **outcome}
                if result["status"] != "retry":
                    OfflineSyncLog.objects.create(
                        id=mutation_id,
                        membership=membership,
                        device_id=device_id,
                        kind=m["kind"][:30],
                        device_time=m["device_time"],
                        result_status=(
                            SyncResultStatus.APPLIED
                            if result["status"] == "applied"
                            else SyncResultStatus.REJECTED
                        ),
                        result_code=result["code"] or "",
                        result_json=result,
                    )
        except IntegrityError:
            # This mutation_id is already taken (e.g. by another organisation),
            # or a client-generated id collided: nothing was applied.
            result = _rejected(mutation_id, "forbidden", "")
        results.append(result)
    return results


def _rejected(mutation_id: UUID, code: str, message: str) -> dict:
    return {
        "mutation_id": str(mutation_id),
        "status": "rejected",
        "code": code,
        "message": message,
        "record": None,
    }
