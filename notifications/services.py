"""Queueing notifications (docs/02-architecture.md §5-6).

Nothing here talks to a provider: it creates Notification rows ("intents"),
each with a dedupe key so the same alert is never queued twice however
often the scan runs. notifications.dispatch decides when and whether each
one goes out (quiet hours, the SMS budget, opt-outs, combining) and sends it
through notifications.backends.get_backend() — the only path to a provider
(CLAUDE.md "Notifications").

send_now() is the exception for a PIN setup code: a one-time code the person
is waiting for, so it goes straight out (still through the same send path,
and still counted against the SMS budget).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from notifications import rules
from notifications.models import Notification, NotificationKind, NotificationStatus
from notifications.sms import render_sms
from organisations.models import Location, Membership


def dedupe_key(kind: str, target_type: str, target_id: Any, step: int, recipient_id: Any) -> str:
    return f"{kind}:{target_type}:{target_id}:{step}:{recipient_id}"


def build(
    *,
    recipient: Membership,
    kind: str,
    context: dict[str, Any],
    send_after: datetime,
    target_type: str = "",
    target_id: UUID | None = None,
    step: int = 0,
    key: str | None = None,
) -> Notification:
    """An unsaved intent. The body is rendered now (≤ 160 chars, SMS links
    from settings.SITE_URL); dispatch may rewrite it when combining."""
    body = render_sms(kind, {**context, "site_url": settings.SITE_URL})
    return Notification(
        organisation_id=recipient.organisation_id,
        recipient=recipient,
        to_e164=recipient.user.phone_e164,
        channel=recipient.user.preferred_channel,
        kind=kind,
        target_type=target_type,
        target_id=target_id,
        step=step,
        body=body[:160],
        dedupe_key=key or dedupe_key(kind, target_type, target_id, step, recipient.id),
        status=NotificationStatus.QUEUED,
        send_after=send_after,
    )


def queue(notifications: list[Notification]) -> int:
    """Saves the intents that don't exist yet; returns how many. Safe to
    call repeatedly and concurrently (unique dedupe_key, ignore_conflicts).
    Must run inside the organisation's tenant_context."""
    if not notifications:
        return 0
    keys = [n.dedupe_key for n in notifications]
    existing = set(
        Notification.objects.filter(dedupe_key__in=keys).values_list("dedupe_key", flat=True)
    )
    new = {n.dedupe_key: n for n in notifications if n.dedupe_key not in existing}
    Notification.objects.bulk_create(list(new.values()), ignore_conflicts=True)
    return len(new)


def notify_flag(
    *,
    location: Location,
    target_type: str,
    target_id: UUID,
    title: str,
    raised_by: Membership,
    reason: str,
    now: datetime | None = None,
) -> int:
    """F5.3: a problem was reported — alert the supervisor(s) on duty, but
    not the person who raised it."""
    now = now or timezone.now()
    recipients = [m for m in rules.supervisors_on_duty(location, now) if m.id != raised_by.id]
    moment = int(now.timestamp())  # a task can be flagged again later
    return queue(
        [
            build(
                recipient=m,
                kind=NotificationKind.FLAG,
                context={"staff_name": raised_by.name, "task_title": title, "reason": reason},
                send_after=now,
                target_type=target_type,
                target_id=target_id,
                key=dedupe_key(NotificationKind.FLAG, target_type, target_id, moment, m.id),
            )
            for m in recipients
        ]
    )


def notify_rejected(
    *, task, rejected_by: Membership, reason: str, now: datetime | None = None
) -> int:
    """F5.3: work was sent back — alert whoever completed it."""
    now = now or timezone.now()
    completer = task.completed_by
    if completer is None or completer.id == rejected_by.id:
        return 0
    moment = int(now.timestamp())
    return queue(
        [
            build(
                recipient=completer,
                kind=NotificationKind.REJECTED,
                context={"task_title": task.title, "reason": reason},
                send_after=now,
                target_type="task",
                target_id=task.id,
                key=dedupe_key(NotificationKind.REJECTED, "task", task.id, moment, completer.id),
            )
        ]
    )


def send_now(notification: Notification) -> Notification:
    """Send immediately (a PIN setup code). Same send path as dispatch."""
    from notifications.dispatch import send

    return send(notification, now=timezone.now())
