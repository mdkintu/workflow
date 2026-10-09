"""Shared pytest fixtures."""

import pytest
from django.core.cache import cache

from tests.factories import MembershipFactory, OrganisationFactory


@pytest.fixture(autouse=True)
def _isolate_cache_and_notification_outbox():
    """Login/PIN-setup rate limiting (accounts.ratelimit) lives in the cache,
    and the console notification backend keeps an in-memory outbox — both
    would otherwise leak state between tests."""
    from notifications.backends import console

    cache.clear()
    console.outbox.clear()
    yield
    cache.clear()
    console.outbox.clear()


@pytest.fixture(autouse=True)
def _fast_tests_and_isolated_media(settings, tmp_path):
    """PBKDF2 (the real PIN hasher) dominates test time; tests don't need
    it. Uploaded photos go to a per-test temp dir, served directly (not via
    Caddy's X-Accel-Redirect) unless a test opts in."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.MEDIA_ROOT = str(tmp_path / "media")
    settings.MEDIA_X_ACCEL = False


@pytest.fixture(autouse=True)
def _organisation_local_time():
    """Tests say "today" with timezone.localdate(); the app means the
    organisation's day (Africa/Kampala by default, UTC+3). Without this the
    two disagree between 21:00 and 24:00 UTC every evening."""
    from zoneinfo import ZoneInfo

    from django.utils import timezone

    with timezone.override(ZoneInfo("Africa/Kampala")):
        yield


@pytest.fixture
def org_a(db):
    return OrganisationFactory()


@pytest.fixture
def org_b(db):
    return OrganisationFactory()


@pytest.fixture
def login_as(client):
    """Logs `client` in as a membership's user and activates that membership."""

    def _login_as(membership, pin="1234"):
        client.force_login(membership.user)
        session = client.session
        session["active_membership_id"] = str(membership.id)
        session.save()
        return client

    return _login_as


@pytest.fixture
def manager_membership(org_a):
    return MembershipFactory(organisation=org_a, role="manager")
