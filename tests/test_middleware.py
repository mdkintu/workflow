"""organisations.middleware.TenantMiddleware: docs/02-architecture.md §3."""

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest, HttpResponse

from organisations.middleware import TenantMiddleware
from organisations.tenancy import get_current_org
from tests.factories import MembershipFactory, OrganisationFactory, UserFactory

pytestmark = pytest.mark.django_db


def _request(rf, user) -> HttpRequest:
    request = rf.get("/app/")
    SessionMiddleware(lambda req: HttpResponse()).process_request(request)
    request.user = user
    return request


def test_middleware_sets_current_org_during_the_request_and_resets_after(rf):
    org = OrganisationFactory()
    membership = MembershipFactory(organisation=org, role="staff")
    request = _request(rf, membership.user)
    seen = {}

    def get_response(req):
        seen["org"] = get_current_org()
        return HttpResponse("ok")

    TenantMiddleware(get_response)(request)

    assert seen["org"] == org
    assert get_current_org() is None


def test_middleware_auto_selects_the_single_active_membership(rf):
    org = OrganisationFactory()
    membership = MembershipFactory(organisation=org, role="staff")
    request = _request(rf, membership.user)

    response = TenantMiddleware(lambda req: HttpResponse("ok"))(request)

    assert response.status_code == 200
    assert request.membership == membership
    assert request.organisation == org
    assert request.session["active_membership_id"] == str(membership.id)


def test_middleware_redirects_to_org_switch_with_two_memberships(rf):
    user = UserFactory()
    org_a = OrganisationFactory()
    org_b = OrganisationFactory()
    MembershipFactory(organisation=org_a, user=user, role="staff")
    MembershipFactory(organisation=org_b, user=user, role="staff")
    request = _request(rf, user)

    response = TenantMiddleware(lambda req: HttpResponse("ok"))(request)

    assert response.status_code == 302
    assert response.url == "/org/switch/"


def test_middleware_forbids_a_user_with_no_active_membership(rf):
    user = UserFactory()
    request = _request(rf, user)

    response = TenantMiddleware(lambda req: HttpResponse("ok"))(request)

    assert response.status_code == 403


def test_middleware_skips_anonymous_users(rf):
    from django.contrib.auth.models import AnonymousUser

    request = _request(rf, AnonymousUser())
    response = TenantMiddleware(lambda req: HttpResponse("ok"))(request)

    assert response.status_code == 200
    assert request.membership is None


def test_middleware_activates_the_organisations_timezone_for_the_request(rf):
    from django.utils import timezone

    org = OrganisationFactory(timezone="Africa/Nairobi")
    membership = MembershipFactory(organisation=org, role="staff")
    request = _request(rf, membership.user)
    before = timezone.get_current_timezone_name()
    seen = {}

    def get_response(req):
        seen["tz"] = timezone.get_current_timezone_name()
        return HttpResponse("ok")

    TenantMiddleware(get_response)(request)

    assert seen["tz"] == "Africa/Nairobi"
    assert timezone.get_current_timezone_name() == before  # restored, not reset
