"""TenantMiddleware: resolves the active membership/organisation for every
authenticated request and sets the `current_org` ContextVar for the
duration of the request. See docs/02-architecture.md §3.
"""

from __future__ import annotations

import zoneinfo

from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.utils import timezone

from organisations.tenancy import current_org

# Paths that work without an active organisation: login/logout, PIN setup,
# the org picker itself, health checks, the CA setup page, static assets,
# the PWA shell files, the offline page and the admin (cross-tenant by design).
EXEMPT_PREFIXES = (
    "/login",
    "/logout",
    "/pin",
    "/org/switch",
    "/healthz",
    "/setup",
    "/static",
    "/sw.js",
    "/manifest.json",
    "/offline",
    "/admin",
)


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.organisation = None
        request.membership = None

        if request.path.startswith(EXEMPT_PREFIXES):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)

        membership = self._resolve_membership(request, user)
        if isinstance(membership, HttpResponse):
            return membership
        if membership is None:
            return HttpResponseForbidden(
                "You have no active organisation membership. Ask an owner or manager to add you."
            )

        request.organisation = membership.organisation
        request.membership = membership
        token = current_org.set(membership.organisation)
        try:
            # Due times are entered and shown in the organisation's timezone;
            # the database stays in UTC (docs/02-architecture.md §4.6,
            # ADR-15). override() restores whatever was active before.
            with timezone.override(zoneinfo.ZoneInfo(membership.organisation.timezone)):
                self._touch_activity(request, membership)
                return self.get_response(request)
        finally:
            current_org.reset(token)

    def _touch_activity(self, request: HttpRequest, membership) -> None:
        """ "Active N min ago" on the People cards (ADR-21): one write a minute
        per session and membership at most, not one per request."""
        from organisations.activity import TOUCH_EVERY, touch

        key = f"seen:{membership.id}"
        now = timezone.now()
        last = request.session.get(key)
        if last is not None and now.timestamp() - last < TOUCH_EVERY.total_seconds():
            return
        touch(membership, now)
        request.session[key] = now.timestamp()

    def _resolve_membership(self, request: HttpRequest, user):
        # `.unscoped` is required here, not a shortcut: this method's whole
        # job is to work out which organisation applies, so no current_org
        # exists yet for `Membership.objects` to filter by. CLAUDE.md's rule
        # against `.unscoped` targets request-handling code that already
        # knows its tenant (views/serializers/forms/sync); this middleware
        # is what establishes the tenant in the first place.
        from organisations.models import Membership

        active_id = request.session.get("active_membership_id")
        if active_id:
            membership = (
                Membership.unscoped.select_related("organisation")
                .filter(id=active_id, user=user, is_active=True)
                .first()
            )
            if membership is not None:
                return membership
            request.session.pop("active_membership_id", None)

        active_memberships = list(
            Membership.unscoped.select_related("organisation")
            .filter(user=user, is_active=True)
            .order_by("id")[:2]
        )
        if len(active_memberships) == 1:
            membership = active_memberships[0]
            request.session["active_membership_id"] = str(membership.id)
            return membership
        if len(active_memberships) > 1:
            return redirect("organisations:org-switch")
        return None
