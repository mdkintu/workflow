"""Alerts queued straight away by events (docs/01-requirements.md F5.3): a
problem reported on a task or checklist run alerts the supervisor on duty;
work sent back alerts whoever completed it."""

from datetime import time, timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from checklists import runs
from notifications.models import Notification, NotificationKind
from tasks.transitions import Action, apply
from tests.factories import (
    ChecklistRunFactory,
    ChecklistRunItemFactory,
    LocationFactory,
    MembershipFactory,
    MembershipLocationFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
    TaskFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def w(org_a):
    main = LocationFactory(organisation=org_a)
    staff = MembershipFactory(organisation=org_a, role="staff", user__name="Peter")
    supervisor = MembershipFactory(organisation=org_a, role="supervisor")
    MembershipLocationFactory(organisation=org_a, membership=supervisor, location=main)
    task = TaskFactory(
        organisation=org_a,
        location=main,
        assignee_location=False,
        assignee_membership=staff,
        photo_required=False,
    )
    return SimpleNamespace(org=org_a, main=main, staff=staff, supervisor=supervisor, task=task)


def test_reporting_a_problem_alerts_the_supervisor(w):
    apply(w.task, Action.FLAG, w.staff, reason="No supplies")
    n = Notification.unscoped.get(kind=NotificationKind.FLAG)
    assert n.recipient == w.supervisor
    assert "Peter" in n.body and "No supplies" in n.body


def test_sending_work_back_alerts_whoever_did_it(w):
    apply(w.task, Action.COMPLETE, w.staff)
    apply(w.task, Action.REJECT, w.supervisor, reason="Bed not made")
    n = Notification.unscoped.get(kind=NotificationKind.REJECTED)
    assert n.recipient == w.staff
    assert "Bed not made" in n.body


def test_a_problem_on_a_checklist_alerts_the_supervisor(w):
    shift = ShiftFactory(
        organisation=w.org, location=w.main, start_time=time(0), end_time=time(23, 59)
    )
    ShiftAssignmentFactory(
        organisation=w.org, shift=shift, membership=w.staff, date=timezone.localdate()
    )
    run = ChecklistRunFactory(
        organisation=w.org,
        location=w.main,
        shift=shift,
        shift_date=timezone.localdate(),
        occurrence_start=timezone.now() - timedelta(minutes=5),
        due_at=timezone.now() + timedelta(hours=1),
    )
    ChecklistRunItemFactory(organisation=w.org, run=run)
    runs.flag_run(run, w.staff, reason="No supplies")
    assert Notification.unscoped.get(kind=NotificationKind.FLAG).recipient == w.supervisor


def test_the_person_who_raised_it_is_not_alerted_about_it(w):
    apply(w.task, Action.FLAG, w.supervisor, reason="Seen it myself")
    assert not Notification.unscoped.filter(kind=NotificationKind.FLAG).exists()
