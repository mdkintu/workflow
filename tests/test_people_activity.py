"""X.5 people cards with activity (ADR-21): "last seen" is recorded at most
once a minute, kept off the synced Membership row, and shown with a presence
dot and the task the person is working on."""

from datetime import timedelta

import pytest
from django.utils import timezone

from organisations.activity import presence
from organisations.models import MemberActivity, Membership
from tasks.models import TaskStatus
from tests.factories import LocationFactory, MembershipFactory, TaskFactory

pytestmark = pytest.mark.django_db


def test_x5_presence_thresholds():
    now = timezone.now()
    assert presence(None, now) == "offline"
    assert presence(now - timedelta(minutes=4), now) == "online"
    assert presence(now - timedelta(minutes=30), now) == "away"
    assert presence(now - timedelta(hours=2), now) == "offline"


def test_x5_a_request_records_last_seen_at_most_once_a_minute(org_a, login_as):
    manager = MembershipFactory(organisation=org_a, role="manager")
    client = login_as(manager)

    client.get("/tasks/my/")
    activity = MemberActivity.unscoped.get(membership=manager)
    assert activity.last_seen_at is not None

    old = timezone.now() - timedelta(hours=3)
    MemberActivity.unscoped.filter(pk=activity.pk).update(last_seen_at=old)
    client.get("/tasks/my/")  # same minute, same session: no write
    assert MemberActivity.unscoped.get(pk=activity.pk).last_seen_at == old


def test_x5_recording_activity_does_not_bump_the_synced_membership_row(org_a, login_as):
    manager = MembershipFactory(organisation=org_a, role="manager")
    seq_before = Membership.unscoped.get(pk=manager.pk).updated_seq
    login_as(manager).get("/tasks/my/")
    assert Membership.unscoped.get(pk=manager.pk).updated_seq == seq_before


def test_x5_people_cards_show_activity_and_the_current_task(org_a, org_b, login_as):
    manager = MembershipFactory(organisation=org_a, role="manager")
    peter = MembershipFactory(organisation=org_a, role="staff", user__name="Peter Okello")
    MembershipFactory(organisation=org_b, role="staff", user__name="Bob In OrgB")
    MemberActivity.unscoped.create(
        organisation=org_a, membership=peter, last_seen_at=timezone.now() - timedelta(minutes=2)
    )
    TaskFactory(
        organisation=org_a,
        location=LocationFactory(organisation=org_a),
        created_by=manager,
        title="Night round, block B",
        status=TaskStatus.IN_PROGRESS,
        started_by=peter,
    )

    body = login_as(manager).get("/org/people/").content.decode()

    assert body.count('data-testid="person-card"') == 2  # manager + Peter
    assert "Peter Okello" in body
    assert "Working on: Night round, block B" in body
    assert "Active 2" in body
    assert "dot-success" in body
    assert "Bob In OrgB" not in body
