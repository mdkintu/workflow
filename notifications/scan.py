"""The per-minute scan (docs/01-requirements.md F5.1-F5.2, docs/02-
architecture.md §5): finds work reaching its reminder time, becoming overdue,
or reaching an escalation step, and queues one intent per alert per person.

    task reminder      lead time before due (task's own, else the org default;
                       0 = off) → the assignee, or for a shift/location task
                       the supervisor on duty
    task overdue       at due time → the same people
    escalation step 1  due + delay → supervisors on duty (not anyone already
                       alerted at step 0)
    escalation step 2  due + 2 × delay → the location's managers
    run overdue        at due time → supervisors on duty
    run escalation     due + delay → the location's managers

Idempotent: the dedupe keys mean re-scanning (every minute) queues nothing
twice. Work more than LOOKBACK overdue is left alone, so turning alerts on
for an existing organisation doesn't send a storm about old tasks.
"""

from __future__ import annotations

import zoneinfo
from datetime import datetime, timedelta

from django.utils import timezone

from checklists.models import OPEN_RUN_STATUSES, ChecklistRun
from notifications import rules
from notifications.models import Notification, NotificationKind
from notifications.services import build, queue
from organisations.models import Organisation
from organisations.tenancy import tenant_context
from tasks.models import OPEN_STATUSES, Task

LOOKBACK = timedelta(hours=24)
REMINDER_MAX_LEAD = timedelta(minutes=120)  # the largest lead the task form offers


def scan_org(organisation: Organisation, *, now: datetime | None = None) -> int:
    """Queues any alerts now due for one organisation. Returns how many."""
    now = now or timezone.now()
    tz = zoneinfo.ZoneInfo(organisation.timezone)
    delay = timedelta(minutes=organisation.escalation_delay_min)
    intents: list[Notification] = []

    with tenant_context(organisation):
        duty_cache: dict = {}
        manager_cache: dict = {}

        def duty(location):
            if location.id not in duty_cache:
                duty_cache[location.id] = rules.supervisors_on_duty(location, now)
            return duty_cache[location.id]

        def managers(location):
            if location.id not in manager_cache:
                manager_cache[location.id] = rules.managers_for(location)
            return manager_cache[location.id]

        def add(kind, step, recipients, target, target_type, context):
            intents.extend(
                build(
                    recipient=recipient,
                    kind=kind,
                    context=context,
                    send_after=now,
                    target_type=target_type,
                    target_id=target.id,
                    step=step,
                )
                for recipient in recipients
            )

        tasks = Task.objects.filter(
            status__in=OPEN_STATUSES,
            due_at__gte=now - LOOKBACK,
            due_at__lte=now + REMINDER_MAX_LEAD,
        ).select_related("location", "assignee_membership__user")
        for task in tasks:
            context = {
                "task_title": task.title,
                "due_time": timezone.localtime(task.due_at, tz).strftime("%H:%M"),
                "location_name": task.location.name,
            }
            first = (
                [task.assignee_membership] if task.assignee_membership_id else duty(task.location)
            )
            lead = (
                task.reminder_lead_min
                if task.reminder_lead_min is not None
                else organisation.default_reminder_lead_min
            )
            if lead and task.due_at - timedelta(minutes=lead) <= now < task.due_at:
                add(NotificationKind.REMINDER, 0, first, task, "task", context)
            if task.due_at <= now:
                add(NotificationKind.OVERDUE, 0, first, task, "task", context)
            if now >= task.due_at + delay:
                already = {m.id for m in first}
                supervisors = [m for m in duty(task.location) if m.id not in already]
                add(NotificationKind.ESCALATION, 1, supervisors, task, "task", context)
            if now >= task.due_at + 2 * delay:
                add(NotificationKind.ESCALATION, 2, managers(task.location), task, "task", context)

        runs = ChecklistRun.objects.filter(
            status__in=OPEN_RUN_STATUSES, due_at__gte=now - LOOKBACK, due_at__lte=now
        ).select_related("location")
        for run in runs:
            context = {
                "task_title": run.name,
                "due_time": timezone.localtime(run.due_at, tz).strftime("%H:%M"),
                "location_name": run.location.name,
            }
            add(NotificationKind.OVERDUE, 0, duty(run.location), run, "run", context)
            if now >= run.due_at + delay:
                add(NotificationKind.ESCALATION, 1, managers(run.location), run, "run", context)

        return queue(intents)
