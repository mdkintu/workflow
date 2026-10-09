"""organisations.permissions.can()/has_role(): docs/04-design.md §2."""

import pytest

from organisations.permissions import Role, can, has_role
from tests.factories import MembershipFactory, OrganisationFactory

pytestmark = pytest.mark.django_db


def test_role_ordering():
    assert Role.OWNER > Role.MANAGER > Role.SUPERVISOR > Role.STAFF


def test_has_role_none_membership_is_false():
    assert has_role(None, Role.STAFF) is False


@pytest.mark.parametrize(
    ("role", "action", "expected"),
    [
        ("staff", "task.view", True),
        ("staff", "task.create", False),
        ("supervisor", "task.create", True),
        ("supervisor", "dashboard.view", True),
        ("staff", "dashboard.view", False),
        ("manager", "membership.invite", True),
        ("supervisor", "membership.invite", False),
        ("owner", "org.settings.edit", True),
        ("manager", "org.settings.edit", False),
    ],
)
def test_can_enforces_the_minimum_role_per_action(role, action, expected):
    org = OrganisationFactory()
    membership = MembershipFactory(organisation=org, role=role)
    assert can(membership, action) is expected


def test_can_denies_an_inactive_membership():
    org = OrganisationFactory()
    membership = MembershipFactory(organisation=org, role="owner", is_active=False)
    assert can(membership, "org.settings.edit") is False


def test_can_denies_none_membership():
    assert can(None, "org.settings.edit") is False


def test_can_denies_an_unknown_action():
    org = OrganisationFactory()
    membership = MembershipFactory(organisation=org, role="owner")
    assert can(membership, "not.a.real.action") is False


def test_can_denies_an_object_from_another_organisation():
    org_a = OrganisationFactory()
    org_b = OrganisationFactory()
    manager = MembershipFactory(organisation=org_a, role="manager")
    other_org_membership = MembershipFactory(organisation=org_b, role="staff")

    assert can(manager, "pin.reset", obj=other_org_membership) is False


@pytest.mark.parametrize(
    ("actor_role", "target_role", "expected"),
    [
        ("supervisor", "staff", True),
        ("supervisor", "supervisor", False),
        ("supervisor", "manager", False),
        ("supervisor", "owner", False),
        ("manager", "supervisor", True),
        ("manager", "manager", True),
        ("owner", "manager", True),
    ],
)
def test_pin_reset_supervisor_is_limited_to_staff(actor_role, target_role, expected):
    """docs/04-design.md §2: Supervisor's pin.reset scope is "locs, Staff
    only"; Manager and Owner keep the plain min-role check."""
    org = OrganisationFactory()
    actor = MembershipFactory(organisation=org, role=actor_role)
    target = MembershipFactory(organisation=org, role=target_role)
    assert can(actor, "pin.reset", obj=target) is expected
