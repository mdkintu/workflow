"""The offline field app's server side (docs/02-architecture.md §4.2-4.3):
the app shell, the versioned service worker, the page-weight budget, and
logout clearing the phone's copy."""

import gzip
import json
import re

import pytest
from django.contrib.staticfiles import finders

from tests.factories import MembershipFactory, OrganisationFactory
from workflow import views

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(org_a):
    return MembershipFactory(organisation=org_a, role="staff", user__name="Peter Okello")


def test_the_shell_holds_no_personal_data(staff, login_as):
    """The service worker caches it; who is logged in comes from /api/sync/me."""
    body = login_as(staff).get("/app/").content.decode()
    assert "Peter Okello" not in body
    assert staff.user.phone_e164 not in body
    assert str(staff.id) not in body


def test_the_shell_sets_the_csrf_cookie_and_carries_its_strings(staff, login_as):
    response = login_as(staff).get("/app/")
    assert "csrftoken" in response.cookies
    strings = json.loads(
        re.search(
            r'id="wf-strings" type="application/json">(.*?)</script>',
            response.content.decode(),
            re.S,
        ).group(1)
    )
    assert strings["codes"]["not_found"]
    assert strings["status"]["in_progress"]


def test_the_service_worker_precaches_the_field_app_under_a_content_hash(client):
    body = client.get("/sw.js").content.decode()
    version = re.search(r'const VERSION = "([0-9a-f]{12})"', body).group(1)
    precache = json.loads(re.search(r"const PRECACHE = (\[.*?\]);", body).group(1))
    assert "/static/field/app.js" in precache
    assert "/static/vendor/dexie.min.js" in precache
    assert "/offline/" in precache
    assert version == views.asset_version()
    # Only root-relative URLs (ADR-18).
    assert all(url.startswith("/") and "//" not in url for url in precache)


def test_the_version_changes_when_a_precached_file_changes(monkeypatch, settings):
    settings.DEBUG = True
    before = views.asset_version()
    real = finders.find

    def fake_find(path, *args, **kwargs):
        if path == "field/app.js":
            return __file__  # different bytes
        return real(path, *args, **kwargs)

    monkeypatch.setattr(views.finders, "find", fake_find)
    assert views.asset_version() != before


def test_the_field_app_fits_the_page_budget():
    """CLAUDE.md: ≤ 250 KB of JS + CSS compressed for the field app; the
    Prompt-3 rule: no page over ~150 KB. Measured gzipped, as Caddy serves it."""
    total = sum(
        len(gzip.compress(open(finders.find(path), "rb").read()))
        for path in views.PRECACHE_STATIC
        if path.endswith((".js", ".css"))
    )
    assert total < 150 * 1024, total


def test_logout_clears_the_phones_copy(staff, login_as):
    response = login_as(staff).post("/logout/")
    assert response.status_code == 302
    assert response["Clear-Site-Data"] == '"cache", "storage"'


def test_managers_still_land_on_the_dashboard(login_as):
    manager = MembershipFactory(organisation=OrganisationFactory(), role="manager")
    assert login_as(manager).get("/").url == "/dashboard/"
