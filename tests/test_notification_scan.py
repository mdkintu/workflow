"""notifications.scan: turning due and overdue work into notification intents
(docs/01-requirements.md F5.1-F5.2, docs/02-architecture.md §5). Every test
passes an explicit `now` (Tuesday 14 Oct 2031, 10:00 in Kampala)."""

from datetime import datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from checklists.models import ChecklistRunStatus
from notifications.models import Notification, NotificationKind, NotificationStatus
from notifications.scan import scan_org
from notifications.tasks import scan_due
from tasks.models import TaskStatus
from tests.factories import (
    ChecklistRunFactory,
    LocationFactory,
    MembershipFactory,
    OrganisationFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
    TaskFactory,
)

pytestmark = pytest.mark.django_db

KLA = ZoneInfo("Africa/Kampala")
NOW = datetime(2031, 10, 14, 10, 0, tzinfo=KLA)


@pytest.fixture
def w(org_a):
    main = LocationFactory(organisation=org_a, name="Main building")
    morning = ShiftFactory(
        organisation=org_a, location=main, name="Morning", start_time=time(6), end_time=time(14)
    )
    world = SimpleNamespace(
        org=org_a,
        main=main,
        morning=morning,
        staff=MembershipFactory(organisation=org_a, role="staff", user__name="Peter"),
        supervisor=MembershipFactory(organisation=org_a, role="supervisor"),
        manager=MembershipFactory(organisation=org_a, role="manager"),
    )
    ShiftAssignmentFactory(
        organisation=org_a, shift=morning, membership=world.supervisor, date=NOW.date()
    )
    return world


def _task(w, due, **kw):
    kw.setdefault("assignee_location", False)
    kw.setdefault("assignee_membership", w.staff)
    return TaskFactory(organisation=w.org, location=w.main, created_by=w.manager, due_at=due, **kw)


def _intents(org):
    return {
        (n.kind, n.step, n.recipient_id) for n in Notification.unscoped.filter(organisation=org)
    }


def test_a_reminder_goes_to_the_assignee_inside_the_lead_time(w):
    task = _task(w, NOW + timedelta(minutes=20))  # org default lead: 30 min
    assert scan_org(w.org, now=NOW) == 1
    notification = Notification.unscoped.get(organisation=w.org)
    assert notification.kind == NotificationKind.REMINDER
    assert notification.recipient == w.staff
    assert notification.target_id == task.id
    assert notification.status == NotificationStatus.QUEUED
    assert task.title[:20] in notification.body
    assert len(notification.body) <= 160


def test_no_reminder_before_the_lead_time_or_when_switched_off(w):
    _task(w, NOW + timedelta(minutes=45))
    _task(w, NOW + timedelta(minutes=10), reminder_lead_min=0)
    assert scan_org(w.org, now=NOW) == 0


def test_a_task_can_ask_for_an_earlier_reminder(w):
    _task(w, NOW + timedelta(minutes=90), reminder_lead_min=120)
    assert _intents(w.org) == set() and scan_org(w.org, now=NOW) == 1


def test_scanning_again_creates_nothing_new(w):
    _task(w, NOW + timedelta(minutes=20))
    scan_org(w.org, now=NOW)
    assert scan_org(w.org, now=NOW) == 0
    assert scan_org(w.org, now=NOW + timedelta(minutes=1)) == 0


def test_a_shared_tasks_reminder_goes_to_the_supervisor_on_duty(w):
    _task(
        w,
        NOW + timedelta(minutes=20),
        assignee_membership=None,
        assignee_shift=w.morning,
        shift_date=NOW.date(),
    )
    scan_org(w.org, now=NOW)
    assert _intents(w.org) == {(NotificationKind.REMINDER, 0, w.supervisor.id)}


def test_overdue_then_escalates_to_the_supervisor_then_the_manager(w):
    task = _task(w, NOW - timedelta(minutes=5))  # org escalation delay: 30 min
    scan_org(w.org, now=NOW)
    assert _intents(w.org) == {(NotificationKind.OVERDUE, 0, w.staff.id)}

    scan_org(w.org, now=task.due_at + timedelta(minutes=31))
    assert (NotificationKind.ESCALATION, 1, w.supervisor.id) in _intents(w.org)

    scan_org(w.org, now=task.due_at + timedelta(minutes=61))
    assert (NotificationKind.ESCALATION, 2, w.manager.id) in _intents(w.org)
    assert len(_intents(w.org)) == 3


def test_finished_flagged_and_long_overdue_work_is_left_alone(w):
    _task(w, NOW - timedelta(minutes=5), status=TaskStatus.DONE)
    _task(w, NOW - timedelta(minutes=5), status=TaskStatus.CANCELLED)
    _task(w, NOW - timedelta(minutes=5), status=TaskStatus.FLAGGED, flag_kind="problem")
    _task(w, NOW - timedelta(days=2))  # before the 24 h look-back
    assert scan_org(w.org, now=NOW) == 0


def test_an_overdue_checklist_run_alerts_the_supervisor_then_the_manager(w):
    run = ChecklistRunFactory(
        organisation=w.org,
        location=w.main,
        shift=w.morning,
        occurrence_start=NOW - timedelta(hours=1),
        due_at=NOW - timedelta(minutes=5),
    )
    scan_org(w.org, now=NOW)
    assert _intents(w.org) == {(NotificationKind.OVERDUE, 0, w.supervisor.id)}
    scan_org(w.org, now=run.due_at + timedelta(minutes=31))
    assert (NotificationKind.ESCALATION, 1, w.manager.id) in _intents(w.org)


def test_a_finished_run_is_left_alone(w):
    ChecklistRunFactory(
        organisation=w.org,
        location=w.main,
        due_at=NOW - timedelta(minutes=5),
        occurrence_start=NOW - timedelta(hours=1),
        status=ChecklistRunStatus.DONE,
    )
    assert scan_org(w.org, now=NOW) == 0


def test_only_the_scanned_organisation_is_touched(w, org_b):
    TaskFactory(organisation=org_b, due_at=NOW - timedelta(minutes=5))
    scan_org(w.org, now=NOW)
    assert not Notification.unscoped.filter(organisation=org_b).exists()


def test_the_beat_task_fans_out_to_active_organisations(w):
    """Uses the real clock, so the task is due 5 minutes ago in real time."""
    from django.utils import timezone

    _task(w, timezone.now() - timedelta(minutes=5))
    inactive = OrganisationFactory(is_active=False)
    TaskFactory(organisation=inactive, due_at=timezone.now() - timedelta(minutes=5))
    scan_due()
    assert Notification.unscoped.filter(organisation=w.org).exists()
    assert not Notification.unscoped.filter(organisation=inactive).exists()
