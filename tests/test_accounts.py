"""accounts.phone.normalise and accounts.backends.PinBackend (ADR-06)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.backends import PinBackend
from accounts.phone import normalise
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0772 123-456", "+256772123456"),
        ("772123456", "+256772123456"),
        ("+256772123456", "+256772123456"),
        ("256772123456", "+256772123456"),
    ],
)
def test_normalise_accepts_every_documented_shape(raw, expected):
    assert normalise(raw) == expected


@pytest.mark.parametrize("raw", ["", "123", "07721234", "abcdefghi", "0123456789012", None])
def test_normalise_rejects_invalid_numbers(raw):
    with pytest.raises(ValueError):
        normalise(raw)


def test_pin_login_success():
    user = UserFactory(phone_e164="+256772000111", pin="4321")
    backend = PinBackend()
    assert backend.authenticate(None, phone="0772000111", pin="4321") == user


def test_pin_login_wrong_pin_fails():
    UserFactory(phone_e164="+256772000222", pin="4321")
    backend = PinBackend()
    assert backend.authenticate(None, phone="0772000222", pin="0000") is None


def test_lockout_after_five_failed_attempts():
    UserFactory(phone_e164="+256772000333", pin="4321")
    backend = PinBackend()
    for _ in range(5):
        assert backend.authenticate(None, phone="0772000333", pin="wrong") is None

    from accounts.models import User

    user = User.objects.get(phone_e164="+256772000333")
    assert user.failed_pin_attempts == 5
    assert user.locked_until is not None
    assert user.locked_until > timezone.now()


def test_locked_account_rejected_even_with_correct_pin():
    user = UserFactory(phone_e164="+256772000444", pin="4321")
    user.locked_until = timezone.now() + timedelta(minutes=10)
    user.save(update_fields=["locked_until"])

    backend = PinBackend()
    assert backend.authenticate(None, phone="0772000444", pin="4321") is None


def test_successful_login_resets_failed_attempts():
    UserFactory(phone_e164="+256772000555", pin="4321")
    backend = PinBackend()
    backend.authenticate(None, phone="0772000555", pin="wrong")

    from accounts.models import User

    user = User.objects.get(phone_e164="+256772000555")
    assert user.failed_pin_attempts == 1

    assert backend.authenticate(None, phone="0772000555", pin="4321") is not None
    user.refresh_from_db()
    assert user.failed_pin_attempts == 0
    assert user.locked_until is None
