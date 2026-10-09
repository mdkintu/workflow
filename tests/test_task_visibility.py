"""Task.objects.visible_to(membership) and annotate_overdue() — the "mine" /
"locs" / "all" scopes from docs/04-design.md §2 and derived overdue (ADR-17).
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from django.utils import timezone

from organisations.tenancy import tenant_context
from tasks.models import Task, TaskStatus
from tests.factories import (
    LocationFactory,
    MembershipFactory,
    MembershipLocationFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
    TaskFactory,
)

pytestmark = pytest.mark.django_db


def _visible_ids(membership):
    with tenant_context(membership.organisation):
        return set(Task.objects.visible_to(membership).values_list("id", flat=True))


@pytest.fixture
def loc(org_a):
    return LocationFactory(organisation=org_a)


def _task(org, location, **kwargs):
    kwargs.setdefault("assignee_location", False)
    return TaskFactory(organisation=org, location=location, **kwargs)


def test_staff_see_tasks_assigned_to_them_and_not_to_others(org_a, loc):
    me = MembershipFactory(organisation=org_a, role="staff")
    other = MembershipFactory(organisation=org_a, role="staff")
    mine = _task(org_a, loc, assignee_membership=me)
    theirs = _task(org_a, loc, assignee_membership=other)

    ids = _visible_ids(me)
    assert mine.id in ids
    assert theirs.id not in ids


def test_staff_see_a_shift_task_only_when_rostered_on_that_shift_that_day(org_a, loc):
    me = MembershipFactory(organisation=org_a, role="staff")
    shift = ShiftFactory(organisation=org_a, location=loc)
    today = timezone.localdate()
    today_task = _task(org_a, loc, assignee_shift=shift, shift_date=today)
    tomorrow_task = _task(org_a, loc, assignee_shift=shift, shift_date=today + timedelta(days=1))
    ShiftAssignmentFactory(organisation=org_a, shift=shift, membership=me, date=today)

    ids = _visible_ids(me)
    assert today_task.id in ids
    assert tomorrow_task.id not in ids


def test_staff_see_a_location_task_when_rostered_at_that_location_that_local_day(org_a, loc):
    """Organisation timezone is Africa/Kampala (UTC+3): 22:30 UTC on the
    14th is 01:30 on the 15th locally, so it's the 15th's roster that counts."""
    me = MembershipFactory(organisation=org_a, role="staff")
    shift = ShiftFactory(organisation=org_a, location=loc)
    due = datetime(2031, 10, 14, 22, 30, tzinfo=UTC)
    task = _task(org_a, loc, assignee_location=True, due_at=due)

    ShiftAssignmentFactory(organisation=org_a, shift=shift, membership=me, date=date(2031, 10, 14))
    assert task.id not in _visible_ids(me)

    ShiftAssignmentFactory(organisation=org_a, shift=shift, membership=me, date=date(2031, 10, 15))
    assert task.id in _visible_ids(me)


def test_a_supervisor_with_no_linked_locations_sees_everything(org_a, loc):
    supervisor = MembershipFactory(organisation=org_a, role="supervisor")
    task = _task(org_a, loc, assignee_membership=MembershipFactory(organisation=org_a))
    assert task.id in _visible_ids(supervisor)


def test_a_supervisor_with_linked_locations_sees_those_plus_their_own(org_a, loc):
    other_loc = LocationFactory(organisation=org_a)
    supervisor = MembershipFactory(organisation=org_a, role="supervisor")
    MembershipLocationFactory(organisation=org_a, membership=supervisor, location=loc)
    here = _task(org_a, loc, assignee_membership=MembershipFactory(organisation=org_a))
    elsewhere = _task(org_a, other_loc, assignee_membership=MembershipFactory(organisation=org_a))
    own_elsewhere = _task(org_a, other_loc, assignee_membership=supervisor)

    ids = _visible_ids(supervisor)
    assert here.id in ids
    assert elsewhere.id not in ids
    assert own_elsewhere.id in ids


def test_owners_see_everything_in_their_organisation_only(org_a, org_b, loc):
    owner = MembershipFactory(organisation=org_a, role="owner")
    task = _task(org_a, loc, assignee_membership=MembershipFactory(organisation=org_a))
    other_org_task = TaskFactory(organisation=org_b)

    ids = _visible_ids(owner)
    assert task.id in ids
    assert other_org_task.id not in ids


def test_overdue_is_derived_from_due_time_and_status(org_a, loc):
    staff = MembershipFactory(organisation=org_a)
    past = timezone.now() - timedelta(hours=1)
    late_open = _task(org_a, loc, assignee_membership=staff, due_at=past)
    late_done = _task(org_a, loc, assignee_membership=staff, due_at=past, status=TaskStatus.DONE)
    late_flagged = _task(
        org_a,
        loc,
        assignee_membership=staff,
        due_at=past,
        status=TaskStatus.FLAGGED,
        flag_kind="problem",
    )
    not_due = _task(org_a, loc, assignee_membership=staff)

    with tenant_context(org_a):
        overdue = dict(Task.objects.annotate_overdue().values_list("id", "is_overdue"))
    assert overdue[late_open.id] is True
    assert overdue[late_done.id] is False
    assert overdue[late_flagged.id] is False  # flagged counts as flagged, not overdue
    assert overdue[not_due.id] is False
    assert late_open.is_overdue is True
