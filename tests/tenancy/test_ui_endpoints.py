"""Cross-tenant leak tests for the ADR-21 endpoints (CLAUDE.md: every new
endpoint gets one here): the bell's list and "seen", and the task board."""

import pytest
from django.utils import timezone

from organisations.models import MemberActivity
from tests.factories import LocationFactory, MembershipFactory, NotificationFactory, TaskFactory

pytestmark = pytest.mark.django_db


def test_notification_list_never_shows_another_organisations_rows(org_a, org_b, login_as):
    person = MembershipFactory(organisation=org_a, role="manager")
    # The same person, in another organisation, with a notification there.
    other = MembershipFactory(organisation=org_b, role="manager", user=person.user)
    NotificationFactory(organisation=org_b, recipient=other, body="Org B secret")

    body = login_as(person).get("/notifications/").content.decode()

    assert "Org B secret" not in body


def test_marking_seen_only_touches_my_own_organisation(org_a, org_b, login_as):
    person = MembershipFactory(organisation=org_a, role="manager")
    other = MembershipFactory(organisation=org_b, role="manager", user=person.user)

    login_as(person).post("/notifications/seen/")

    assert (
        MemberActivity.unscoped.filter(membership=person)
        .exclude(notifications_seen_at=None)
        .exists()
    )
    assert not MemberActivity.unscoped.filter(membership=other).exists()


def test_the_task_board_never_shows_another_organisations_tasks(org_a, org_b, login_as):
    manager = MembershipFactory(organisation=org_a, role="manager")
    TaskFactory(
        organisation=org_b,
        location=LocationFactory(organisation=org_b),
        created_by=MembershipFactory(organisation=org_b, role="manager"),
        title="Org B task",
        due_at=timezone.now(),
    )
    body = login_as(manager).get("/tasks/?view=board").content.decode()
    assert "Org B task" not in body


def test_another_organisations_task_cannot_be_moved_from_the_board(org_a, org_b, login_as):
    manager = MembershipFactory(organisation=org_a, role="manager")
    task = TaskFactory(
        organisation=org_b,
        location=LocationFactory(organisation=org_b),
        created_by=MembershipFactory(organisation=org_b, role="manager"),
    )
    response = login_as(manager).post(f"/tasks/{task.id}/start/", {"next": "/tasks/?view=board"})
    assert response.status_code == 404
