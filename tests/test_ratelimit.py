"""Per-IP login throttling (docs/01-requirements.md NFR-S3/X.1), on top of
the per-account lockout already covered by tests/test_accounts.py."""

import pytest

from accounts.ratelimit import (
    MAX_ATTEMPTS_PER_IP,
    get_client_ip,
    is_ip_rate_limited,
    record_login_attempt,
)
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_login_is_rate_limited_after_too_many_attempts_from_one_ip(client):
    UserFactory(phone_e164="+256772009000", pin="4321")

    for _ in range(MAX_ATTEMPTS_PER_IP):
        response = client.post("/login/", {"phone": "0772009000", "pin": "wrong"})
        assert response.status_code == 200

    response = client.post("/login/", {"phone": "0772009000", "pin": "4321"})
    assert response.status_code == 429


def test_get_client_ip_prefers_x_forwarded_for_when_trusted(rf, settings):
    settings.TRUST_PROXY_HEADERS = True
    request = rf.post("/login/", HTTP_X_FORWARDED_FOR="10.0.0.9, 172.17.0.1")
    assert get_client_ip(request) == "10.0.0.9"


def test_get_client_ip_ignores_x_forwarded_for_when_untrusted(rf, settings):
    settings.TRUST_PROXY_HEADERS = False
    request = rf.post("/login/", HTTP_X_FORWARDED_FOR="10.0.0.9", REMOTE_ADDR="203.0.113.5")
    assert get_client_ip(request) == "203.0.113.5"


def test_rate_limit_is_scoped_per_ip(rf):
    request_a = rf.post("/login/", REMOTE_ADDR="203.0.113.1")
    request_b = rf.post("/login/", REMOTE_ADDR="203.0.113.2")
    for _ in range(MAX_ATTEMPTS_PER_IP):
        record_login_attempt(request_a, scope="login")

    assert is_ip_rate_limited(request_a, scope="login") is True
    assert is_ip_rate_limited(request_b, scope="login") is False


def test_rate_limit_is_scoped_per_action(rf):
    """The 'login' and 'pin-setup' counters are independent, so a burst of
    failed logins doesn't lock a caller out of setting their PIN."""
    request = rf.post("/login/", REMOTE_ADDR="203.0.113.3")
    for _ in range(MAX_ATTEMPTS_PER_IP):
        record_login_attempt(request, scope="login")

    assert is_ip_rate_limited(request, scope="login") is True
    assert is_ip_rate_limited(request, scope="pin-setup") is False
