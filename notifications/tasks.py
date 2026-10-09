"""Celery tasks for notifications (docs/02-architecture.md §5). Tasks take
IDs, not objects, and are idempotent (CLAUDE.md "Celery"):

    scan_due            every minute: one scan_due_for_org per active org
    dispatch_due        every minute: decide due intents, enqueue sends
    send_notification   on the "notifications" queue, so slow SMS calls
                        never hold up checklist generation
"""

from __future__ import annotations

from celery import shared_task

from notifications import dispatch
from notifications.scan import scan_org
from organisations.models import Organisation


@shared_task(time_limit=60, soft_time_limit=50, acks_late=True)
def scan_due() -> int:
    org_ids = list(Organisation.objects.filter(is_active=True).values_list("id", flat=True))
    for org_id in org_ids:
        scan_due_for_org.delay(str(org_id))
    return len(org_ids)


@shared_task(time_limit=120, soft_time_limit=100, acks_late=True)
def scan_due_for_org(org_id: str) -> int:
    return scan_org(Organisation.objects.get(pk=org_id, is_active=True))


@shared_task(time_limit=120, soft_time_limit=100, acks_late=True)
def dispatch_due() -> int:
    return len(dispatch.dispatch_due())


@shared_task(time_limit=60, soft_time_limit=50, acks_late=True)
def send_notification(notification_id: str) -> str:
    return dispatch.deliver(notification_id)
