"""Compact JSON rows for the phone (docs/04-design.md §4.4). Plain functions,
not DRF serializers: the shapes are fixed and small, and this keeps a pull of
a few hundred rows cheap. Timestamps are ISO-8601 UTC with a trailing Z."""

from __future__ import annotations

from datetime import datetime


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _id(value) -> str | None:
    return str(value) if value is not None else None


def location(obj) -> dict:
    return {
        "id": str(obj.id),
        "name": obj.name,
        "is_active": obj.is_active,
        "sort_order": obj.sort_order,
    }


def person(obj) -> dict:
    return {"id": str(obj.id), "name": obj.name, "role": obj.role, "is_active": obj.is_active}


def shift(obj) -> dict:
    return {
        "id": str(obj.id),
        "location_id": str(obj.location_id),
        "name": obj.name,
        "start": obj.start_time.strftime("%H:%M"),
        "end": obj.end_time.strftime("%H:%M"),
        "weekdays": obj.weekdays,
        "is_active": obj.is_active,
    }


def shift_assignment(obj) -> dict:
    return {
        "id": str(obj.id),
        "shift_id": str(obj.shift_id),
        "membership_id": str(obj.membership_id),
        "date": obj.date.isoformat(),
    }


def task(obj) -> dict:
    if obj.assignee_membership_id:
        assignee = {"type": "membership", "id": str(obj.assignee_membership_id)}
    elif obj.assignee_shift_id:
        assignee = {"type": "shift", "id": str(obj.assignee_shift_id)}
    else:
        assignee = {"type": "location", "id": str(obj.location_id)}
    return {
        "id": str(obj.id),
        "title": obj.title,
        "description": obj.description,
        "location_id": str(obj.location_id),
        "assignee": assignee,
        "shift_date": obj.shift_date.isoformat() if obj.shift_date else None,
        "due_at": iso(obj.due_at),
        "status": obj.status,
        "photo_required": obj.photo_required,
        "completed_by": _id(obj.completed_by_id),
        "completed_at": iso(obj.completed_at_trusted),
        "flag": {"kind": obj.flag_kind, "reason": obj.flag_reason} if obj.flag_kind else None,
    }


def run(obj) -> dict:
    return {
        "id": str(obj.id),
        "name": obj.name,
        "location_id": str(obj.location_id),
        "shift_id": _id(obj.shift_id),
        "shift_date": obj.shift_date.isoformat() if obj.shift_date else None,
        "occurrence_start": iso(obj.occurrence_start),
        "available_from": iso(obj.available_from),
        "due_at": iso(obj.due_at),
        "status": obj.status,
    }


def run_item(obj) -> dict:
    return {
        "id": str(obj.id),
        "run_id": str(obj.run_id),
        "order": obj.order,
        "label": obj.label,
        "photo_required": obj.photo_required,
        "skippable": obj.skippable,
    }


def run_item_tick(obj) -> dict:
    return {
        "id": str(obj.id),
        "run_item_id": str(obj.run_item_id),
        "membership_id": str(obj.membership_id),
        "skipped": obj.skipped,
        "skip_reason": obj.skip_reason,
        "time": iso(obj.trusted_time),
        "photo_id": _id(obj.photo_id),
    }


def comment(obj) -> dict:
    hidden = obj.hidden_by_id is not None
    return {
        "id": str(obj.id),
        "task_id": _id(obj.task_id),
        "run_id": _id(obj.checklist_run_id),
        "author_id": str(obj.author_id),
        "body": "" if hidden else obj.body,
        "is_flag": obj.is_flag,
        "photo_id": _id(obj.photo_id),
        "time": iso(obj.device_time),
        "hidden": hidden,
    }


def photo(obj) -> dict:
    return {
        "id": str(obj.id),
        "task_id": _id(obj.task_id),
        "run_id": _id(obj.checklist_run_id),
        "url": f"/media/p/{obj.id}",
        "thumb_url": f"/media/p/{obj.id}/t",
    }
