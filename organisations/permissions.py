"""Role-based permissions: docs/04-design.md §2.

`can(membership, action, obj=None)` implements the matrix at the role level.
The tenancy guard (an object's organisation must equal the membership's) is
real and always enforced first. Narrower scopes within a role ("locs" for a
Manager's linked locations, "mine" for a Staff member's own work) are left
to view code and `visible_to()` querysets, built with each feature.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import IntEnum
from functools import wraps
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse


class Role(IntEnum):
    """Ordered low to high so `>=` reads as "at least this senior":
    owner > manager > supervisor > staff."""

    STAFF = 0
    SUPERVISOR = 1
    MANAGER = 2
    OWNER = 3

    @classmethod
    def from_value(cls, value: str) -> Role:
        return cls[value.upper()]


def has_role(membership: Any, min_role: Role) -> bool:
    if membership is None:
        return False
    return Role.from_value(membership.role) >= min_role


# Action -> minimum role required, from the docs/04-design.md §2 matrix.
_MIN_ROLE: dict[str, Role] = {
    "org.settings.edit": Role.OWNER,
    "membership.invite": Role.MANAGER,
    "membership.deactivate": Role.MANAGER,
    "membership.change_role": Role.MANAGER,
    "pin.reset": Role.SUPERVISOR,
    "location.manage": Role.MANAGER,
    "shift.manage": Role.MANAGER,
    "roster.view": Role.STAFF,
    "roster.edit": Role.MANAGER,
    "template.manage": Role.MANAGER,
    "task.view": Role.STAFF,
    "task.create": Role.SUPERVISOR,
    "task.create_self": Role.STAFF,
    "task.edit": Role.SUPERVISOR,
    "task.cancel": Role.SUPERVISOR,
    "task.start": Role.STAFF,
    "task.complete": Role.STAFF,
    "task.flag": Role.STAFF,
    "task.reject": Role.SUPERVISOR,
    "task.resolve_flag": Role.SUPERVISOR,
    "run.view": Role.STAFF,
    "run.tick": Role.STAFF,
    "run.skip": Role.STAFF,
    "run.flag": Role.STAFF,
    "run.resolve_flag": Role.SUPERVISOR,
    "run.cancel": Role.SUPERVISOR,
    "comment.add": Role.STAFF,
    "comment.delete_own": Role.STAFF,
    "comment.hide": Role.MANAGER,
    "photo.view": Role.STAFF,
    "dashboard.view": Role.SUPERVISOR,
    "notification.settings": Role.STAFF,
    "audit.view": Role.OWNER,
    "user.export_or_anonymise": Role.OWNER,
}


def can(membership: Any, action: str, obj: Any = None) -> bool:
    if membership is None or not membership.is_active:
        return False

    # Tenancy guard: real, always enforced, comes before the role check.
    if obj is not None:
        obj_org_id = getattr(obj, "organisation_id", None)
        if obj_org_id is not None and obj_org_id != membership.organisation_id:
            return False

    min_role = _MIN_ROLE.get(action)
    if min_role is None:
        return False
    if not has_role(membership, min_role):
        return False

    # A Supervisor may only reset a PIN for Staff; Manager and Owner keep
    # the plain min-role check ("locs"/"all" in the docs/04-design.md §2
    # matrix). organisations.services.reset_pin enforces this too, so it
    # stays correct even when called outside a view.
    if action == "pin.reset" and obj is not None:
        from organisations.models import MembershipRole

        if membership.role == MembershipRole.SUPERVISOR and obj.role != MembershipRole.STAFF:
            return False

    return True


def location_scope(membership: Any) -> list | None:
    """The "locs" scope from docs/04-design.md §2, shared by every
    visible_to() queryset. Returns None for "every location" (Owners, and
    Supervisors/Managers with no linked locations), a list of location ids
    for a Supervisor/Manager with linked locations, or [] for Staff (who only
    ever get the "mine" scope)."""
    from organisations.models import MembershipLocation

    role = Role.from_value(membership.role)
    if role == Role.OWNER:
        return None
    if role < Role.SUPERVISOR:
        return []
    ids = list(
        MembershipLocation.objects.filter(membership=membership).values_list(
            "location_id", flat=True
        )
    )
    return ids or None


def role_required(min_role: Role) -> Callable:
    """View function decorator: raises PermissionDenied (403) unless
    request.membership is at least `min_role`."""

    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not has_role(getattr(request, "membership", None), min_role):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


class RoleRequiredMixin:
    """Class-based-view mixin. Set `min_role = Role.SUPERVISOR` on the view."""

    min_role: Role = Role.STAFF

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not has_role(getattr(request, "membership", None), self.min_role):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
