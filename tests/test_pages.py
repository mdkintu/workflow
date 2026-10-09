"""The scaffold's routes (docs/04-design.md §4.1): health, setup, the PWA
shell files, the field app shell, and the sync API stubs.
"""

import json

import pytest
from django.test import override_settings

from tests.factories import MembershipFactory, OrganisationFactory

pytestmark = pytest.mark.django_db


def test_healthz_ok_even_when_host_not_in_allowed_hosts(client, settings):
    settings.ALLOWED_HOSTS = []
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@override_settings(CA_SETUP_PAGE_ENABLED=False)
def test_setup_page_404_when_disabled(client):
    assert client.get("/setup/").status_code == 404


@override_settings(CA_SETUP_PAGE_ENABLED=True)
def test_setup_page_200_when_enabled(client):
    assert client.get("/setup/").status_code == 200


def test_service_worker_headers(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/javascript"
    assert response["Service-Worker-Allowed"] == "/"
    assert response["Cache-Control"] == "no-cache"


def test_manifest_is_valid_json(client):
    response = client.get("/manifest.json")
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data["name"] == "WorkFlow"
    assert data["start_url"] == "/app/"


def test_app_shell_requires_login(client):
    response = client.get("/app/")
    assert response.status_code == 302
    assert "/login/" in response.url


def test_app_shell_loads_when_logged_in(client, login_as):
    membership = MembershipFactory(organisation=OrganisationFactory(), role="staff")
    login_as(membership)
    assert client.get("/app/").status_code == 200


def test_sync_me_returns_real_data_when_authenticated(client, login_as):
    org = OrganisationFactory(name="Acme Org")
    membership = MembershipFactory(organisation=org, role="staff")
    login_as(membership)

    response = client.get("/api/sync/me")

    assert response.status_code == 200
    data = response.json()
    assert data["membership"]["id"] == str(membership.id)
    assert data["organisation"]["name"] == "Acme Org"


def test_sync_endpoints_require_authentication(client):
    assert client.get("/api/sync/me").status_code == 401
