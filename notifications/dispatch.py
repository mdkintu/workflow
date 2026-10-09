"""Deciding and sending notifications (docs/01-requirements.md F5.5,
docs/02-architecture.md §5-6).

dispatch_due() runs every minute (Beat). It looks at every due intent, across
all organisations, and for each one decides:

    suppress   the work is already finished; a reminder to someone who opted
               out; the monthly SMS cap is reached (except escalations to a
               Manager/Owner); or folded into another message (combining)
    hold       quiet hours, unless the recipient is on shift right now — held
               until quiet hours end
    send       claimed with a LEASE (send_after pushed forward), then handed
               to the `send_notification` Celery task on the "notifications"
               queue

Combining: overdue/escalation alerts for one person due within
COMBINE_WINDOW become a single SMS ("3 tasks overdue at …").

Retries live in the row, not in Celery: a temporary provider failure sets
send_after = now + backoff (1, 2, 4 … 60 min) and the next dispatch picks it
up again; after GIVE_UP_AFTER it's marked failed. The lease means a worker
that dies mid-send just lets the row become due again — nothing is lost,
and a row is never sent by two workers at once.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from checklists.models import OPEN_RUN_STATUSES, ChecklistRun
from notifications import rules
from notifications.backends import get_backend
from notifications.backends.base import Channel, OutboundMessage
from notifications.models import (
    Notification,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
    SmsUsage,
)
from notifications.services import build, dedupe_key, queue
from notifications.sms import render_sms
from organisations.models import Membership, MembershipRole
from organisations.tenancy import tenant_context
from tasks.models import OPEN_STATUSES, Task

LEASE = timedelta(minutes=10)
COMBINE_WINDOW = timedelta(minutes=5)
GIVE_UP_AFTER = timedelta(hours=24)
BATCH_SIZE = 500
BUDGET_WARNING_AT = 0.8

COMBINABLE = {NotificationKind.OVERDUE, NotificationKind.ESCALATION}
ABOUT_OPEN_WORK = {
    NotificationKind.REMINDER,
    NotificationKind.OVERDUE,
    NotificationKind.ESCALATION,
}


def _month(organisation, now: datetime) -> date:
    return timezone.localtime(now, rules._tz(organisation)).date().replace(day=1)


def _still_open_targets(notifications: list[Notification]) -> tuple[set[UUID], dict[UUID, str]]:
    """Which targets are still open, plus each target's location name — two
    queries for the whole batch. System code: the dispatcher spans every
    organisation, so it uses the unscoped managers (CLAUDE.md)."""
    task_ids = {n.target_id for n in notifications if n.target_type == "task"}
    run_ids = {n.target_id for n in notifications if n.target_type == "run"}
    open_ids, location_names = set(), {}
    for task in Task.unscoped.filter(id__in=task_ids).select_related("location"):
        location_names[task.id] = task.location.name
        if task.status in OPEN_STATUSES:
            open_ids.add(task.id)
    for run in ChecklistRun.unscoped.filter(id__in=run_ids).select_related("location"):
        location_names[run.id] = run.location.name
        if run.status in OPEN_RUN_STATUSES:
            open_ids.add(run.id)
    return open_ids, location_names


def _over_budget(notification: Notification, usage: dict, now: datetime) -> bool:
    if notification.channel != NotificationChannel.SMS:
        return False
    organisation = notification.organisation
    key = (organisation.id, _month(organisation, now))
    if key not in usage:
        row = SmsUsage.unscoped.filter(organisation=organisation, month=key[1]).first()
        usage[key] = row.count if row else 0
    if usage[key] < organisation.sms_monthly_cap:
        return False
    escalation_to_management = notification.kind == NotificationKind.ESCALATION and (
        notification.recipient.role in (MembershipRole.MANAGER, MembershipRole.OWNER)
    )
    return not escalation_to_management


def _on_shift(membership: Membership, now: datetime) -> bool:
    with tenant_context(membership.organisation):
        return rules.is_on_shift(membership, now)


def _suppress(notification: Notification, reason: str) -> None:
    notification.status = NotificationStatus.SUPPRESSED
    notification.error = reason[:300]


def dispatch_due(*, now: datetime | None = None, enqueue: bool = True) -> list[UUID]:
    """Decides every due intent and returns the ids claimed for sending
    (enqueued to Celery unless enqueue=False)."""
    now = now or timezone.now()
    claimed: list[Notification] = []

    with transaction.atomic():
        pending = list(
            Notification.unscoped.select_for_update(skip_locked=True, of=("self",))
            .filter(
                status__in=[NotificationStatus.QUEUED, NotificationStatus.HELD],
                send_after__lte=now + COMBINE_WINDOW,
            )
            .select_related("organisation", "recipient__user")
            .order_by("send_after", "created_at")[:BATCH_SIZE]
        )
        open_ids, location_names = _still_open_targets(pending)
        usage: dict = {}
        primaries: dict[UUID, Notification] = {}
        folded: dict[UUID, list[Notification]] = defaultdict(list)
        changed: list[Notification] = []

        due = [n for n in pending if n.send_after <= now]
        early = [n for n in pending if n.send_after > now and n.kind in COMBINABLE]

        for n in due:
            changed.append(n)
            if n.kind in ABOUT_OPEN_WORK and n.target_id not in open_ids:
                _suppress(n, "no longer needed: the work is finished")
            elif n.kind == NotificationKind.REMINDER and n.recipient.user.reminders_opt_out:
                _suppress(n, "the recipient turned reminders off")
            elif _over_budget(n, usage, now):
                _suppress(n, "monthly SMS limit reached")
            elif rules.in_quiet_hours(n.organisation, now) and not _on_shift(n.recipient, now):
                n.status = NotificationStatus.HELD
                n.send_after = rules.quiet_end(n.organisation, now)
            elif n.kind in COMBINABLE and n.recipient_id in primaries:
                primary = primaries[n.recipient_id]
                _suppress(n, f"combined into {primary.id}")
                folded[primary.id].append(n)
            else:
                if n.kind in COMBINABLE:
                    primaries[n.recipient_id] = n
                n.status = NotificationStatus.QUEUED
                n.send_after = now + LEASE  # the claim
                claimed.append(n)

        # Pull forward alerts due in the next few minutes for someone who is
        # getting one now, so they arrive as one message.
        for n in early:
            primary = primaries.get(n.recipient_id)
            if primary is not None and n.target_id in open_ids:
                _suppress(n, f"combined into {primary.id}")
                folded[primary.id].append(n)
                changed.append(n)

        for primary in primaries.values():
            extra = folded.get(primary.id)
            if extra:
                primary.body = render_sms(
                    "overdue_combined",
                    {
                        "count": 1 + len(extra),
                        "location_name": location_names.get(primary.target_id, ""),
                        "due_time": _due_time(primary),
                        "site_url": settings.SITE_URL,
                    },
                )[:160]

        for n in changed:
            n.save(update_fields=["status", "send_after", "error", "body", "updated_at"])

    ids = [n.id for n in claimed]
    if enqueue:
        from notifications.tasks import send_notification

        for notification_id in ids:
            send_notification.delay(str(notification_id))
    return ids


def _due_time(notification: Notification) -> str:
    target = None
    if notification.target_type == "task":
        target = Task.unscoped.filter(pk=notification.target_id).only("due_at").first()
    elif notification.target_type == "run":
        target = ChecklistRun.unscoped.filter(pk=notification.target_id).only("due_at").first()
    if target is None:
        return ""
    return timezone.localtime(target.due_at, rules._tz(notification.organisation)).strftime("%H:%M")


def deliver(notification_id: UUID | str, *, now: datetime | None = None) -> str:
    """Sends one claimed notification. Returns its new status."""
    now = now or timezone.now()
    with transaction.atomic():
        notification = (
            Notification.unscoped.select_for_update(of=("self",))
            .select_related("organisation", "recipient__user")
            .get(pk=notification_id)
        )
        if notification.status != NotificationStatus.QUEUED:
            return notification.status  # already sent, suppressed or given up
        return send(notification, now=now).status


def _message(notification: Notification, channel: str) -> OutboundMessage:
    return OutboundMessage(
        to=notification.to_e164,
        body=notification.body,
        channel=Channel(channel),
        dedupe_key=notification.dedupe_key,
        organisation_id=notification.organisation_id,
    )


def send(notification: Notification, *, now: datetime) -> Notification:
    """The one path to a provider (CLAUDE.md "Notifications"): the
    recipient's channel, falling back from WhatsApp to SMS on a permanent
    failure (docs/02-architecture.md §6); records the outcome; schedules a
    retry with backoff for a temporary failure."""
    channel = notification.channel
    backend = get_backend(channel)
    result = backend.send(_message(notification, channel)) if backend else None

    whatsapp_unusable = backend is None or (not result.accepted and not result.retryable)
    if channel == NotificationChannel.WHATSAPP and whatsapp_unusable:
        channel = NotificationChannel.SMS
        backend = get_backend(channel)
        result = backend.send(_message(notification, channel)) if backend else None

    notification.channel = channel
    if result is None:
        _suppress(notification, "no notification backend is configured")
        notification.save(update_fields=["status", "error", "channel", "updated_at"])
        return notification

    notification.attempts += 1
    notification.provider_message_id = result.provider_message_id or ""
    notification.cost = result.cost
    if result.accepted:
        notification.status = NotificationStatus.SENT
        notification.sent_at = now
        notification.error = ""
    elif result.retryable and now - notification.created_at < GIVE_UP_AFTER:
        notification.status = NotificationStatus.QUEUED
        notification.send_after = now + rules.backoff(notification.attempts)
        notification.error = (result.error or "")[:300]
    else:
        notification.status = NotificationStatus.FAILED
        notification.error = (result.error or "")[:300]
    notification.save(
        update_fields=[
            "status",
            "sent_at",
            "send_after",
            "attempts",
            "provider_message_id",
            "cost",
            "error",
            "channel",
            "updated_at",
        ]
    )
    if result.accepted and channel == NotificationChannel.SMS:
        _count_sms(notification, now)
    return notification


def _count_sms(notification: Notification, now: datetime) -> None:
    """Counts a sent SMS against the monthly cap, and warns the owners once
    when 80 % is reached (docs/01-requirements.md F5.5)."""
    organisation = notification.organisation
    with tenant_context(organisation):
        usage, _ = SmsUsage.objects.get_or_create(month=_month(organisation, now))
        SmsUsage.objects.filter(pk=usage.pk).update(count=F("count") + 1)
        usage.refresh_from_db()
        if usage.warned_80 or usage.count < BUDGET_WARNING_AT * organisation.sms_monthly_cap:
            return
        usage.warned_80 = True
        usage.save(update_fields=["warned_80", "updated_at"])
        owners = Membership.objects.filter(
            role=MembershipRole.OWNER, is_active=True
        ).select_related("user")
        queue(
            [
                build(
                    recipient=owner,
                    kind=NotificationKind.BUDGET_WARNING,
                    context={"used": usage.count, "cap": organisation.sms_monthly_cap},
                    send_after=now,
                    key=dedupe_key(
                        NotificationKind.BUDGET_WARNING, "month", usage.month, 0, owner.id
                    ),
                )
                for owner in owners
            ]
        )
