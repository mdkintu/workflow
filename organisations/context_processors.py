"""Navigation links for base.html, from the same permission helpers the
views use (CLAUDE.md: no ad-hoc role checks in templates)."""

from __future__ import annotations

from django.http import HttpRequest

from organisations.permissions import Role, can, has_role

# (namespace, url_name or None for "any") -> sidebar item key, first match wins.
_CURRENT = (
    ("tasks", "my", "my"),
    ("tasks", None, "tasks"),
    ("checklists", None, "checklists"),
    ("roster", None, "roster"),
    ("dashboard", None, "dashboard"),
    ("organisations", "people", "people"),
    ("organisations", "people-invite", "people"),
    ("organisations", None, "setup"),
    ("", "styleguide", "styleguide"),
)


def _current(request: HttpRequest) -> str:
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    for namespace, url_name, key in _CURRENT:
        if match.namespace == namespace and url_name in (None, match.url_name):
            return key
    return ""


def navigation(request: HttpRequest) -> dict:
    membership = getattr(request, "membership", None)
    if membership is None:
        return {}
    from notifications.inbox import has_unseen

    return {
        "nav": {
            "tasks": can(membership, "task.create"),
            "roster": has_role(membership, Role.SUPERVISOR),
            "checklists": has_role(membership, Role.SUPERVISOR),
            "dashboard": can(membership, "dashboard.view"),
            "people": can(membership, "membership.invite"),
            "setup": can(membership, "location.manage"),
        },
        "nav_current": _current(request),
        # Evaluated only if a template uses it (the bell in base.html).
        "has_unseen_notifications": lambda: has_unseen(membership),
    }
