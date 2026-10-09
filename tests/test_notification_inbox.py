"""F5.6 notification list (ADR-21): the header bell lists my own recent
notifications; opening it marks them seen."""

from datetime import timedelta

import pytest
from django.utils import timezone

from notifications.models import Notification, NotificationStatus
from tests.factories import MembershipFactory, NotificationFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def manager(org_a):
    return MembershipFactory(organisation=org_a, role="manager")


def test_f5_6_the_list_shows_only_my_recent_notifications(org_a, manager, login_as):
    NotificationFactory(organisation=org_a, recipient=manager, body="Room 12 is overdue")
    NotificationFactory(
        organisation=org_a,
        recipient=manager,
        body="Held back",
        status=NotificationStatus.SUPPRESSED,
    )
    old = NotificationFactory(organisation=org_a, recipient=manager, body="Last month")
    Notification.unscoped.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=8))
    colleague = MembershipFactory(organisation=org_a, role="staff")
    NotificationFactory(organisation=org_a, recipient=colleague, body="Not for me")

    body = login_as(manager).get("/notifications/").content.decode()

    assert "Room 12 is overdue" in body
    assert "Held back" not in body
    assert "Last month" not in body
    assert "Not for me" not in body


def test_f5_6_the_red_dot_clears_once_the_list_is_seen(org_a, manager, login_as):
    client = login_as(manager)
    assert 'id="notif-dot"' not in client.get("/tasks/my/").content.decode()

    NotificationFactory(organisation=org_a, recipient=manager)
    assert 'id="notif-dot"' in client.get("/tasks/my/").content.decode()

    assert client.post("/notifications/seen/").status_code == 204
    assert 'id="notif-dot"' not in client.get("/tasks/my/").content.decode()


def test_f5_6_marking_seen_needs_a_post(manager, login_as):
    assert login_as(manager).get("/notifications/seen/").status_code == 405


def test_f5_6_logged_out_users_are_sent_to_login(client):
    response = client.get("/notifications/")
    assert response.status_code == 302
    assert "/login/" in response.url
