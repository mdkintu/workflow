"""Checklist templates, their items and schedules (docs/01-requirements.md
F2.1-F2.2). Every change is audited (NFR-S11). Adding or resuming a schedule
generates its runs straight away, so a manager sees them without waiting for
the next Beat pass; pausing one cancels its future, untouched runs (docs/04-
design.md §5.4).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from checklists.generator import generate_runs
from checklists.models import (
    ChecklistItem,
    ChecklistRun,
    ChecklistRunStatus,
    ChecklistTemplate,
    RecurrenceRule,
)
from organisations.models import AuditEvent, Location, Membership, Shift
from organisations.tenancy import tenant_context


def _audit(actor: Membership, action: str, target, changes: dict[str, Any]) -> None:
    AuditEvent.objects.create(
        actor=actor,
        action=action,
        target_type=target._meta.model_name,
        target_id=target.id,
        changes=changes,
    )


def create_template(
    *, actor: Membership, name: str, location: Location, shift: Shift | None
) -> ChecklistTemplate:
    with tenant_context(actor.organisation):
        template = ChecklistTemplate.objects.create(
            name=name, location=location, shift=shift, created_by=actor
        )
        _audit(actor, "template.create", template, {"name": [None, name]})
    return template


def update_template(
    *,
    actor: Membership,
    template: ChecklistTemplate,
    name: str,
    shift: Shift | None,
    is_active: bool,
) -> ChecklistTemplate:
    changes = {}
    for field, new in (("name", name), ("shift", shift), ("is_active", is_active)):
        old = getattr(template, field)
        if old != new:
            changes[field] = [str(old) if old is not None else None, str(new) if new else new]
            setattr(template, field, new)
    if changes:
        with tenant_context(actor.organisation):
            template.save()
            _audit(actor, "template.edit", template, changes)
    return template


def add_item(
    *,
    actor: Membership,
    template: ChecklistTemplate,
    label: str,
    photo_required: bool = False,
    skippable: bool = True,
) -> ChecklistItem:
    with tenant_context(actor.organisation), transaction.atomic():
        last = ChecklistItem.objects.filter(template=template).aggregate(m=Max("order"))["m"]
        item = ChecklistItem.objects.create(
            template=template,
            order=(last or 0) + 1,
            label=label,
            photo_required=photo_required,
            skippable=skippable,
        )
        _audit(actor, "template.item_add", template, {"item": [None, label]})
    return item


def update_item(*, actor: Membership, item: ChecklistItem, **fields: Any) -> ChecklistItem:
    changes = {}
    for field in ("label", "photo_required", "skippable"):
        if field in fields and getattr(item, field) != fields[field]:
            changes[field] = [getattr(item, field), fields[field]]
            setattr(item, field, fields[field])
    if changes:
        with tenant_context(actor.organisation):
            item.save()
            _audit(actor, "template.item_edit", item.template, changes)
    return item


def move_item(*, actor: Membership, item: ChecklistItem, direction: str) -> None:
    """Swaps with the neighbouring active item. The (template, order) unique
    constraint is deferred, so the swap is valid at commit."""
    with tenant_context(actor.organisation), transaction.atomic():
        siblings = ChecklistItem.objects.filter(template_id=item.template_id, is_active=True)
        if direction == "up":
            neighbour = siblings.filter(order__lt=item.order).order_by("-order").first()
        else:
            neighbour = siblings.filter(order__gt=item.order).order_by("order").first()
        if neighbour is None:
            return
        item.order, neighbour.order = neighbour.order, item.order
        item.save(update_fields=["order", "updated_at"])
        neighbour.save(update_fields=["order", "updated_at"])
        _audit(actor, "template.item_move", item.template, {"item": [item.label, direction]})


def remove_item(*, actor: Membership, item: ChecklistItem) -> None:
    """Soft-remove: runs already generated keep their copy of the item."""
    with tenant_context(actor.organisation):
        item.is_active = False
        item.save(update_fields=["is_active", "updated_at"])
        _audit(actor, "template.item_remove", item.template, {"item": [item.label, None]})


def add_rule(*, actor: Membership, template: ChecklistTemplate, fields: dict[str, Any]) -> int:
    """Creates a schedule and returns how many runs it generated now."""
    with tenant_context(actor.organisation):
        rule = RecurrenceRule.objects.create(template=template, **fields)
        _audit(actor, "template.rule_add", template, {"rule": [None, str(rule.id)]})
    return generate_runs(actor.organisation)


def set_rule_active(
    *, actor: Membership, rule: RecurrenceRule, active: bool, now: datetime | None = None
) -> int:
    """Pausing cancels the rule's runs that haven't started yet and aren't
    due to start yet — today's run someone is already working on stays.
    Resuming generates again. Returns runs cancelled or created."""
    now = now or timezone.now()
    with tenant_context(actor.organisation):
        rule.is_active = active
        rule.save(update_fields=["is_active", "updated_at"])
        _audit(
            actor,
            "template.rule_resume" if active else "template.rule_pause",
            rule.template,
            {"rule": [str(rule.id), active]},
        )
        if active:
            return generate_runs(actor.organisation, now=now)
        return ChecklistRun.objects.filter(
            rule=rule, status=ChecklistRunStatus.PENDING, occurrence_start__gt=now
        ).update(status=ChecklistRunStatus.CANCELLED, updated_at=now)
