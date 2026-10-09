"""notifications.dispatch: deciding and sending due notifications
(docs/01-requirements.md F5.5, docs/02-architecture.md §5-6): quiet hours,
the monthly SMS cap, opt-outs, combining, retry with backoff, and the
WhatsApp → SMS fallback."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from notifications import dispatch
from notifications.backends import console
from notifications.backends.base import SendResult
from notifications.models import (
    Notification,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
    SmsUsage,
)
from tasks.models import Task, TaskStatus
from tests.factories import (
    LocationFactory,
    MembershipFactory,
    NotificationFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
    SmsUsageFactory,
    TaskFactory,
)

pytestmark = pytest.mark.django_db

KLA = ZoneInfo("Africa/Kampala")
NOON = datetime(2031, 10, 14, 12, 0, tzinfo=KLA)
NIGHT = datetime(2031, 10, 14, 23, 0, tzinfo=KLA)


@pytest.fixture
def w(org_a):
    main = LocationFactory(organisation=org_a)
    staff = MembershipFactory(organisation=org_a, role="staff")
    manager = MembershipFactory(organisation=org_a, role="manager")
    task = TaskFactory(
        organisation=org_a,
        location=main,
        assignee_location=False,
        assignee_membership=staff,
        created_by=manager,
        due_at=NOON - timedelta(minutes=10),
    )
    return SimpleNamespace(org=org_a, main=main, staff=staff, manager=manager, task=task)


def _queue(w, recipient=None, kind=NotificationKind.OVERDUE, send_after=NOON, **kw):
    recipient = recipient or w.staff
    kw.setdefault("target_type", "task")
    kw.setdefault("target_id", w.task.id)
    n = NotificationFactory(
        organisation=w.org,
        recipient=recipient,
        to_e164=recipient.user.phone_e164,
        kind=kind,
        send_after=send_after,
        body=f"{kind} body",
        **kw,
    )
    # Created "now" on the test's clock (the give-up window counts from it).
    Notification.unscoped.filter(pk=n.pk).update(created_at=send_after)
    n.refresh_from_db()
    return n


def _run(now):
    """Dispatch as Beat would, then deliver what it claimed (eager Celery
    would do the same inline)."""
    for notification_id in dispatch.dispatch_due(now=now, enqueue=False):
        dispatch.deliver(notification_id, now=now)


def _status(n):
    n.refresh_from_db()
    return n.status


def test_a_due_notification_is_sent_and_counted(w):
    n = _queue(w)
    _run(NOON)
    n.refresh_from_db()
    assert n.status == NotificationStatus.SENT
    assert n.sent_at is not None and n.attempts == 1
    assert [m.to for m in console.outbox] == [w.staff.user.phone_e164]
    assert SmsUsage.unscoped.get(organisation=w.org, month=date(2031, 10, 1)).count == 1


def test_one_not_yet_due_is_left_for_later(w):
    n = _queue(w, send_after=NOON + timedelta(minutes=30))
    _run(NOON)
    assert _status(n) == NotificationStatus.QUEUED
    assert console.outbox == []


def test_quiet_hours_hold_it_until_morning(w):
    n = _queue(w, send_after=NIGHT)
    _run(NIGHT)
    n.refresh_from_db()
    assert n.status == NotificationStatus.HELD
    assert n.send_after == datetime(2031, 10, 15, 6, 0, tzinfo=KLA)
    assert console.outbox == []

    _run(datetime(2031, 10, 15, 6, 1, tzinfo=KLA))
    assert _status(n) == NotificationStatus.SENT


def test_someone_on_shift_during_quiet_hours_still_gets_it(w):
    night = ShiftFactory(
        organisation=w.org, location=w.main, name="Night", start_time=time(22), end_time=time(6)
    )
    ShiftAssignmentFactory(organisation=w.org, shift=night, membership=w.staff, date=NIGHT.date())
    n = _queue(w, send_after=NIGHT)
    _run(NIGHT)
    assert _status(n) == NotificationStatus.SENT


def test_nothing_is_sent_about_work_that_is_already_done(w):
    Task.unscoped.filter(pk=w.task.pk).update(status=TaskStatus.DONE)
    n = _queue(w)
    _run(NOON)
    n.refresh_from_db()
    assert n.status == NotificationStatus.SUPPRESSED
    assert console.outbox == []


def test_a_reminder_respects_the_persons_opt_out_but_an_escalation_does_not(w):
    for m in (w.staff, w.manager):
        m.user.reminders_opt_out = True
        m.user.save()
    reminder = _queue(w, kind=NotificationKind.REMINDER)
    escalation = _queue(w, recipient=w.manager, kind=NotificationKind.ESCALATION, step=2)
    _run(NOON)
    assert _status(reminder) == NotificationStatus.SUPPRESSED
    assert _status(escalation) == NotificationStatus.SENT


def test_at_the_monthly_cap_only_escalations_to_managers_go_out(w):
    SmsUsageFactory(
        organisation=w.org, month=date(2031, 10, 1), count=w.org.sms_monthly_cap, warned_80=True
    )
    to_staff = _queue(w)
    to_manager = _queue(w, recipient=w.manager, kind=NotificationKind.ESCALATION, step=2)
    _run(NOON)
    to_staff.refresh_from_db()
    assert to_staff.status == NotificationStatus.SUPPRESSED
    assert "limit" in to_staff.error
    assert _status(to_manager) == NotificationStatus.SENT


def test_owners_are_warned_once_at_80_percent(w):
    owner = MembershipFactory(organisation=w.org, role="owner")
    cap = w.org.sms_monthly_cap
    SmsUsageFactory(organisation=w.org, month=date(2031, 10, 1), count=int(cap * 0.8) - 1)
    _queue(w)
    _run(NOON)  # this send reaches 80 %
    _run(NOON + timedelta(minutes=1))  # the warning goes out
    warnings = Notification.unscoped.filter(kind=NotificationKind.BUDGET_WARNING)
    assert [n.recipient_id for n in warnings] == [owner.id]
    assert warnings[0].status == NotificationStatus.SENT
    _queue(w, target_id=TaskFactory(organisation=w.org).id)
    _run(NOON + timedelta(minutes=2))
    assert warnings.count() == 1


def test_several_overdue_alerts_for_one_person_become_one_sms(w):
    first = _queue(w)
    others = [
        _queue(
            w,
            target_id=TaskFactory(organisation=w.org, location=w.main).id,
            send_after=NOON + timedelta(minutes=m),
        )
        for m in (1, 3)  # after `first`, so it leads (equal times would tie)
    ]
    _run(NOON)
    assert len(console.outbox) == 1
    assert "3 tasks overdue" in console.outbox[0].body
    assert _status(first) == NotificationStatus.SENT
    for n in others:
        n.refresh_from_db()
        assert n.status == NotificationStatus.SUPPRESSED
        assert n.error == f"combined into {first.id}"


class ScriptedBackend:
    channels = frozenset({"sms", "whatsapp"})

    def __init__(self, *results):
        self.results = list(results)
        self.sent = []

    def send(self, message):
        self.sent.append(message)
        return self.results.pop(0)


def _result(accepted, retryable=False):
    return SendResult(
        accepted=accepted,
        provider_message_id="x" if accepted else None,
        status="sent" if accepted else "failed",
        cost=Decimal("0.0100") if accepted else None,
        error=None if accepted else "boom",
        retryable=retryable,
    )


def test_a_temporary_failure_is_retried_with_backoff(w, monkeypatch):
    backend = ScriptedBackend(_result(False, retryable=True), _result(True))
    monkeypatch.setattr(dispatch, "get_backend", lambda channel: backend)
    n = _queue(w)
    _run(NOON)
    n.refresh_from_db()
    assert n.status == NotificationStatus.QUEUED
    assert n.attempts == 1
    assert n.send_after == NOON + timedelta(minutes=1)

    _run(NOON + timedelta(seconds=30))
    assert len(backend.sent) == 1  # not due again yet
    _run(NOON + timedelta(minutes=1))
    n.refresh_from_db()
    assert n.status == NotificationStatus.SENT
    assert n.attempts == 2
    assert n.cost == Decimal("0.0100")


def test_a_permanent_failure_is_not_retried(w, monkeypatch):
    monkeypatch.setattr(dispatch, "get_backend", lambda channel: ScriptedBackend(_result(False)))
    n = _queue(w)
    _run(NOON)
    assert _status(n) == NotificationStatus.FAILED


def test_retrying_stops_after_24_hours(w, monkeypatch):
    backend = ScriptedBackend(_result(False, retryable=True))
    monkeypatch.setattr(dispatch, "get_backend", lambda channel: backend)
    n = _queue(w)
    Notification.unscoped.filter(pk=n.pk).update(created_at=NOON - timedelta(hours=25))
    _run(NOON)
    assert _status(n) == NotificationStatus.FAILED


def test_a_claimed_notification_is_not_picked_twice(w):
    n = _queue(w)
    first = dispatch.dispatch_due(now=NOON, enqueue=False)
    second = dispatch.dispatch_due(now=NOON, enqueue=False)
    assert first == [n.id]
    assert second == []


def test_whatsapp_falls_back_to_sms_while_it_is_not_set_up(w, settings):
    settings.WHATSAPP_BACKEND = "stub"
    w.staff.user.preferred_channel = "whatsapp"
    w.staff.user.save()
    n = _queue(w, channel=NotificationChannel.WHATSAPP)
    _run(NOON)
    n.refresh_from_db()
    assert n.status == NotificationStatus.SENT
    assert n.channel == NotificationChannel.SMS
    assert [m.channel for m in console.outbox] == ["sms"]
