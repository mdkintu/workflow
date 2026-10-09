"""PIN format rules (docs/01-requirements.md X.1, Q4's assumed default):
4 digits for a staff-only account, 6 digits once any of the user's active
memberships — in any organisation — is Supervisor or more senior. A PIN is
one per User, shared across every organisation they belong to (X.4), so the
strictest role anywhere wins.

Applied wherever a PIN is actually *set* (accounts.views.pin_setup_view),
never at login: a wrong-format PIN there just fails to match the hash.
"""

from __future__ import annotations

from django.utils.translation import gettext as _

from accounts.models import User
from organisations.models import Membership
from organisations.permissions import Role


def required_pin_length(user: User) -> int:
    # `.unscoped`, deliberately: this checks every organisation the user
    # belongs to, not just one tenant (CLAUDE.md's rule targets code that
    # already knows its tenant; PIN policy is inherently cross-tenant, like
    # organisations.middleware's own membership lookups).
    memberships = Membership.unscoped.filter(user=user, is_active=True)
    is_senior = any(Role.from_value(m.role) >= Role.SUPERVISOR for m in memberships)
    return 6 if is_senior else 4


def validate_pin(pin: str, user: User) -> str | None:
    """Returns a translated error message, or None if `pin` is acceptable."""
    length = required_pin_length(user)
    if not pin.isdigit():
        return _("PIN must be numbers only.")
    if len(pin) != length:
        return _("Your PIN must be exactly %(n)d digits.") % {"n": length}
    return None
