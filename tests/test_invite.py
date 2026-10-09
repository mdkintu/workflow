"""organisations.services.invite_member / reset_pin: a manager adds staff by
phone (docs/01-requirements.md X.3); one phone can join a second
organisation (X.4); resetting a PIN respects the permission matrix's
Supervisor-limited-to-Staff scope (docs/04-design.md §2)."""

import pytest

from accounts.models import PinSetupToken, User
from notifications.backends import console
from organisations.models import AuditEvent
from organisations.services import InviteError, invite_member, reset_pin
from organisations.tenancy import tenant_context
from tests.factories import MembershipFactory, OrganisationFactory

pytestmark = pytest.mark.django_db


def test_invite_creates_a_new_user_and_membership_and_sends_a_code(org_a, manager_membership):
    membership = invite_member(
        actor=manager_membership, phone="0772020000", name="Peter", role="staff"
    )

    assert membership.organisation_id == org_a.id
    assert membership.role == "staff"
    user = membership.user
    assert user.phone_e164 == "+256772020000"
    assert user.name == "Peter"
    assert user.must_set_pin is True
    assert not user.has_usable_password()
    assert PinSetupToken.objects.filter(user=user).exists()
    assert len(console.outbox) == 1
    assert AuditEvent.unscoped.filter(action="membership.invite", target_id=membership.id).exists()


def test_inviting_an_existing_phone_reuses_the_same_user_across_organisations(org_a):
    org_b = OrganisationFactory()
    manager_a = MembershipFactory(organisation=org_a, role="manager")
    manager_b = MembershipFactory(organisation=org_b, role="manager")

    with tenant_context(org_a):
        membership_a = invite_member(
            actor=manager_a, phone="0772020001", name="Grace", role="staff"
        )
    with tenant_context(org_b):
        membership_b = invite_member(
            actor=manager_b, phone="0772020001", name="Grace", role="supervisor"
        )

    assert membership_a.user_id == membership_b.user_id
    assert User.objects.filter(phone_e164="+256772020001").count() == 1


def test_inviting_the_same_phone_twice_into_one_org_is_rejected(manager_membership):
    invite_member(actor=manager_membership, phone="0772020002", name="Aisha", role="staff")
    with pytest.raises(InviteError):
        invite_member(actor=manager_membership, phone="0772020002", name="Aisha", role="staff")


def test_a_manager_cannot_invite_an_owner(manager_membership):
    with pytest.raises(InviteError):
        invite_member(actor=manager_membership, phone="0772020003", name="Sam", role="owner")


def test_an_owner_can_invite_an_owner(org_a):
    owner = MembershipFactory(organisation=org_a, role="owner")
    membership = invite_member(actor=owner, phone="0772020004", name="Sam", role="owner")
    assert membership.role == "owner"


def test_invalid_phone_number_raises(manager_membership):
    with pytest.raises(ValueError, match="not a valid phone number"):
        invite_member(actor=manager_membership, phone="123", name="Bad", role="staff")


def test_reset_pin_issues_a_new_code(org_a):
    manager = MembershipFactory(organisation=org_a, role="manager")
    staff = MembershipFactory(organisation=org_a, role="staff")

    reset_pin(actor=manager, target=staff)

    assert PinSetupToken.objects.filter(user=staff.user).count() == 1
    assert len(console.outbox) == 1
    assert AuditEvent.unscoped.filter(action="pin.reset", target_id=staff.id).exists()


def test_supervisor_cannot_reset_pin_for_another_supervisor(org_a):
    supervisor = MembershipFactory(organisation=org_a, role="supervisor")
    other_supervisor = MembershipFactory(organisation=org_a, role="supervisor")
    with pytest.raises(InviteError):
        reset_pin(actor=supervisor, target=other_supervisor)
    assert not PinSetupToken.objects.filter(user=other_supervisor.user).exists()


def test_supervisor_can_reset_pin_for_staff(org_a):
    supervisor = MembershipFactory(organisation=org_a, role="supervisor")
    staff = MembershipFactory(organisation=org_a, role="staff")
    reset_pin(actor=supervisor, target=staff)
    assert PinSetupToken.objects.filter(user=staff.user).exists()
