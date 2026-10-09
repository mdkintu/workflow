"""Materialises recurring checklist runs ahead of time (ADR-08,
docs/02-architecture.md §5, docs/01-requirements.md F2.2).

Runs are created up to HORIZON ahead, so a phone that goes offline already
holds the checklists it will need. Occurrences are computed in the
organisation's timezone. Generation is idempotent: the (rule,
occurrence_start) unique constraint means a second pass — or a concurrent
Beat run — creates nothing new.

An occurrence is created only while it's still open (its due time hasn't
passed) — a rule added at 10:00 doesn't produce an already-overdue 07:00 run.
"""

from __future__ import annotations

import zoneinfo
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from checklists.models import (
    ChecklistItem,
    ChecklistRun,
    ChecklistRunItem,
    RecurrenceKind,
    RecurrenceRule,
)
from organisations.models import Organisation, Shift
from organisations.tenancy import tenant_context

HORIZON = timedelta(hours=48)


@dataclass(frozen=True)
class Occurrence:
    start: datetime
    shift: Shift | None
    shift_date: date | None


def _days(first: date, last: date) -> Iterator[date]:
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def occurrences(
    rule: RecurrenceRule, tz: zoneinfo.ZoneInfo, window_start: datetime, window_end: datetime
) -> Iterator[Occurrence]:
    """Occurrences of `rule` whose run is still open at `window_start` (due
    after it) and which start no later than `window_end`."""
    template = rule.template
    shift = template.shift if template.shift_id else None
    due_offset = timedelta(minutes=rule.due_offset_min)

    first = max(window_start.astimezone(tz).date() - timedelta(days=1), rule.starts_on)
    last = window_end.astimezone(tz).date()
    if rule.ends_on is not None:
        last = min(last, rule.ends_on)

    for day in _days(first, last):
        if rule.kind == RecurrenceKind.SHIFT_START:
            if shift is None or day.weekday() not in shift.weekdays:
                continue
            times: list[time] = [shift.start_time]
        elif rule.kind == RecurrenceKind.WEEKLY and day.weekday() not in rule.weekdays:
            continue
        else:
            times = rule.times

        for at in times:
            start = datetime.combine(day, at, tzinfo=tz)
            if start + due_offset <= window_start or start > window_end:
                continue
            yield Occurrence(
                start=start, shift=shift, shift_date=day if shift is not None else None
            )


def generate_runs(
    organisation: Organisation, *, now: datetime | None = None, horizon: timedelta = HORIZON
) -> int:
    """Creates any missing runs for `organisation`. Returns how many."""
    now = now or timezone.now()
    tz = zoneinfo.ZoneInfo(organisation.timezone)
    created = 0

    with tenant_context(organisation):
        rules = RecurrenceRule.objects.filter(
            is_active=True, template__is_active=True
        ).select_related("template__location", "template__shift")
        for rule in rules:
            template = rule.template
            if template.shift_id and not template.shift.is_active:
                continue
            items = list(
                ChecklistItem.objects.filter(template=template, is_active=True).order_by("order")
            )
            if not items:
                continue

            existing = set(
                ChecklistRun.objects.filter(
                    rule=rule, occurrence_start__gte=now - timedelta(days=2)
                ).values_list("occurrence_start", flat=True)
            )
            for occurrence in occurrences(rule, tz, now, now + horizon):
                if occurrence.start not in existing:
                    created += _create_run(rule, occurrence, items)
    return created


def _create_run(rule: RecurrenceRule, occurrence: Occurrence, items: list[ChecklistItem]) -> int:
    template = rule.template
    try:
        with transaction.atomic():
            run = ChecklistRun.objects.create(
                template=template,
                rule=rule,
                name=template.name,
                location=template.location,
                shift=occurrence.shift,
                shift_date=occurrence.shift_date,
                occurrence_start=occurrence.start,
                due_at=occurrence.start + timedelta(minutes=rule.due_offset_min),
            )
            # Copied, so editing the template later never rewrites history.
            # bulk_create skips TenantModel.save(), so organisation is explicit.
            ChecklistRunItem.objects.bulk_create(
                ChecklistRunItem(
                    organisation_id=run.organisation_id,
                    run=run,
                    order=position,
                    label=item.label,
                    photo_required=item.photo_required,
                    skippable=item.skippable,
                )
                for position, item in enumerate(items, start=1)
            )
    except IntegrityError:
        return 0  # a concurrent pass created it first
    return 1
