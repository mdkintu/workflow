"""accounts.views.pin_setup_view (S2): first-time PIN setup and a
manager/supervisor-triggered reset share this one flow, keyed by phone +
one-time code (docs/04-design.md §3 S2, §1.2 PinSetupToken)."""

from datetime import timedelta

import pytest
from django.contrib.auth.hashers import make_password
from django.utils import timezone

from accounts.models import User
from accounts.pin import required_pin_length
from tests.factories import (
    MembershipFactory,
    OrganisationFactory,
    PinSetupTokenFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def _issue_token(user, code="135790", **kwargs):
    return PinSetupTokenFactory(user=user, code_hash=make_password(code), **kwargs)


def test_valid_code_sets_the_pin_and_logs_the_user_in(client):
    org = OrganisationFactory()
    user = UserFactory(phone_e164="+256772010000", must_set_pin=True)
    MembershipFactory(organisation=org, user=user, role="staff")
    _issue_token(user)

    response = client.post(
        "/pin/setup/",
        {"phone": "0772010000", "code": "135790", "pin": "1234", "pin_confirm": "1234"},
    )

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.check_password("1234")
    assert user.must_set_pin is False
    assert user.failed_pin_attempts == 0
    assert client.session["_auth_user_id"] == str(user.id)


def test_the_code_can_only_be_used_once(client):
    org = OrganisationFactory()
    user = UserFactory(phone_e164="+256772010001", must_set_pin=True)
    MembershipFactory(organisation=org, user=user, role="staff")
    _issue_token(user)

    client.post(
        "/pin/setup/",
        {"phone": "0772010001", "code": "135790", "pin": "1234", "pin_confirm": "1234"},
    )
    client.logout()

    response = client.post(
        "/pin/setup/",
        {"phone": "0772010001", "code": "135790", "pin": "5678", "pin_confirm": "5678"},
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.check_password("1234")  # unchanged
    assert not user.check_password("5678")


def test_wrong_code_is_rejected(client):
    user = UserFactory(phone_e164="+256772010002", pin="0000")
    _issue_token(user)

    response = client.post(
        "/pin/setup/",
        {"phone": "0772010002", "code": "000000", "pin": "1234", "pin_confirm": "1234"},
    )
    assert response.status_code == 200
    assert not User.objects.get(phone_e164="+256772010002").check_password("1234")


def test_expired_code_is_rejected(client):
    user = UserFactory(phone_e164="+256772010003", pin="0000")
    _issue_token(user, expires_at=timezone.now() - timedelta(hours=1))

    response = client.post(
        "/pin/setup/",
        {"phone": "0772010003", "code": "135790", "pin": "1234", "pin_confirm": "1234"},
    )
    assert response.status_code == 200
    assert not User.objects.get(phone_e164="+256772010003").check_password("1234")


def test_unknown_phone_number_gives_the_same_generic_error(client):
    """No user-enumeration: a phone with no account gets the same message
    as a wrong code for a real one."""
    response = client.post(
        "/pin/setup/",
        {"phone": "0772019999", "code": "135790", "pin": "1234", "pin_confirm": "1234"},
    )
    assert response.status_code == 200
    assert not User.objects.filter(phone_e164="+256772019999").exists()


def test_mismatched_pins_are_rejected(client):
    user = UserFactory(phone_e164="+256772010004", pin="0000")
    _issue_token(user)
    response = client.post(
        "/pin/setup/",
        {"phone": "0772010004", "code": "135790", "pin": "1234", "pin_confirm": "4321"},
    )
    assert response.status_code == 200
    assert not User.objects.get(phone_e164="+256772010004").check_password("1234")


def test_a_supervisors_pin_must_be_six_digits():
    org = OrganisationFactory()
    user = UserFactory()
    MembershipFactory(organisation=org, user=user, role="supervisor")
    assert required_pin_length(user) == 6


def test_a_staff_only_users_pin_is_four_digits():
    org = OrganisationFactory()
    user = UserFactory()
    MembershipFactory(organisation=org, user=user, role="staff")
    assert required_pin_length(user) == 4


def test_a_user_with_any_senior_membership_needs_six_digits():
    """One PIN per person, shared across every organisation they belong to
    (docs/01-requirements.md X.4) — the strictest role wins."""
    org_a = OrganisationFactory()
    org_b = OrganisationFactory()
    user = UserFactory()
    MembershipFactory(organisation=org_a, user=user, role="staff")
    MembershipFactory(organisation=org_b, user=user, role="manager")
    assert required_pin_length(user) == 6


def test_pin_setup_rejects_a_pin_of_the_wrong_length_for_the_role(client):
    org = OrganisationFactory()
    user = UserFactory(phone_e164="+256772010005", pin="0000")
    MembershipFactory(organisation=org, user=user, role="supervisor")
    _issue_token(user)

    response = client.post(
        "/pin/setup/",
        {"phone": "0772010005", "code": "135790", "pin": "1234", "pin_confirm": "1234"},
    )
    assert response.status_code == 200
    assert not User.objects.get(phone_e164="+256772010005").check_password("1234")


def test_pin_setup_rejects_non_digit_pins(client):
    user = UserFactory(phone_e164="+256772010006")
    _issue_token(user)
    response = client.post(
        "/pin/setup/",
        {"phone": "0772010006", "code": "135790", "pin": "abcd", "pin_confirm": "abcd"},
    )
    assert response.status_code == 200
    assert not User.objects.get(phone_e164="+256772010006").check_password("abcd")
