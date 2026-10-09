"""HTTP views for /org/people/... (docs/04-design.md §4.1): the manager-
facing member list, invite form and reset-PIN action."""

import pytest

from notifications.backends import console
from organisations.models import Membership
from tests.factories import MembershipFactory

pytestmark = pytest.mark.django_db


def test_people_list_requires_manager_role(org_a, login_as):
    staff = MembershipFactory(organisation=org_a, role="staff")
    client = login_as(staff)
    response = client.get("/org/people/")
    assert response.status_code == 403


def test_people_list_shows_only_the_current_organisations_members(org_a, org_b, login_as):
    manager = MembershipFactory(organisation=org_a, role="manager")
    MembershipFactory(organisation=org_a, user__name="Alice In OrgA", role="staff")
    MembershipFactory(organisation=org_b, user__name="Bob In OrgB", role="staff")

    client = login_as(manager)
    response = client.get("/org/people/")

    body = response.content.decode()
    assert "Alice In OrgA" in body
    assert "Bob In OrgB" not in body


def test_invite_view_creates_a_membership_and_sends_a_code(org_a, login_as):
    manager = MembershipFactory(organisation=org_a, role="manager")
    client = login_as(manager)

    response = client.post(
        "/org/people/invite/",
        {"phone": "0772030000", "name": "New Staffer", "role": "staff"},
    )

    assert response.status_code == 302
    # .unscoped: checking from the test's own vantage point, after the
    # request (and its tenant_context) has already ended — system code,
    # like the factories (tests/factories.py's own docstring).
    assert Membership.unscoped.filter(organisation=org_a, user__phone_e164="+256772030000").exists()
    assert len(console.outbox) == 1


def test_invite_view_requires_manager_role(org_a, login_as):
    supervisor = MembershipFactory(organisation=org_a, role="supervisor")
    client = login_as(supervisor)
    response = client.post(
        "/org/people/invite/", {"phone": "0772030001", "name": "X", "role": "staff"}
    )
    assert response.status_code == 403


def test_invite_view_hides_the_owner_choice_from_a_manager(org_a, login_as):
    manager = MembershipFactory(organisation=org_a, role="manager")
    client = login_as(manager)
    response = client.get("/org/people/invite/")
    assert b'value="owner"' not in response.content


def test_invite_view_offers_the_owner_choice_to_an_owner(org_a, login_as):
    owner = MembershipFactory(organisation=org_a, role="owner")
    client = login_as(owner)
    response = client.get("/org/people/invite/")
    assert b'value="owner"' in response.content


def test_invite_view_shows_a_friendly_error_for_a_duplicate_member(org_a, login_as):
    manager = MembershipFactory(organisation=org_a, role="manager")
    MembershipFactory(organisation=org_a, user__phone_e164="+256772030002", role="staff")
    client = login_as(manager)

    response = client.post(
        "/org/people/invite/",
        {"phone": "0772030002", "name": "Dup", "role": "staff"},
    )
    assert response.status_code == 200
    assert b"already a member" in response.content


def test_reset_pin_view_requires_supervisor_or_above(org_a, login_as):
    staff = MembershipFactory(organisation=org_a, role="staff")
    target = MembershipFactory(organisation=org_a, role="staff")
    client = login_as(staff)
    response = client.post(f"/org/people/{target.id}/reset-pin/")
    assert response.status_code == 403


def test_reset_pin_view_404s_for_a_membership_in_another_organisation(org_a, org_b, login_as):
    manager = MembershipFactory(organisation=org_a, role="manager")
    target_in_other_org = MembershipFactory(organisation=org_b, role="staff")
    client = login_as(manager)
    response = client.post(f"/org/people/{target_in_other_org.id}/reset-pin/")
    assert response.status_code == 404


def test_supervisor_cannot_reset_pin_for_a_manager_via_the_view(org_a, login_as):
    supervisor = MembershipFactory(organisation=org_a, role="supervisor")
    other_manager = MembershipFactory(organisation=org_a, role="manager")
    client = login_as(supervisor)
    response = client.post(f"/org/people/{other_manager.id}/reset-pin/")
    assert response.status_code == 403


def test_reset_pin_view_succeeds_for_a_manager_resetting_staff(org_a, login_as):
    manager = MembershipFactory(organisation=org_a, role="manager")
    staff = MembershipFactory(organisation=org_a, role="staff")
    client = login_as(manager)
    response = client.post(f"/org/people/{staff.id}/reset-pin/")
    assert response.status_code == 302
    assert len(console.outbox) == 1
