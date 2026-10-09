"""issue_pin_setup_code: the one-time SMS code behind both first-time PIN
setup and a manager/supervisor-triggered reset (accounts.views.pin_setup_view,
docs/04-design.md §1.2 PinSetupToken). Kept in `accounts` because it only
touches accounts.User/PinSetupToken; organisations.services.invite_member and
.reset_pin (which also create/touch a Membership) call into this.
"""

from __future__ import annotations

import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.utils import timezone

from accounts.models import PinSetupToken, User
from notifications.models import Notification, NotificationKind, NotificationStatus
from notifications.services import send_now
from notifications.sms import render_sms
from organisations.models import Membership

CODE_LENGTH = 6
CODE_VALID_HOURS = 24


def issue_pin_setup_code(
    *, user: User, created_by: User | None, recipient: Membership
) -> PinSetupToken:
    """Creates a token, sends the SMS, and returns the token (the raw code
    itself is never stored or returned — only its hash)."""
    code = "".join(secrets.choice(string.digits) for _ in range(CODE_LENGTH))
    token = PinSetupToken.objects.create(
        user=user,
        code_hash=make_password(code),
        expires_at=timezone.now() + timedelta(hours=CODE_VALID_HOURS),
        created_by=created_by,
    )

    body = render_sms("pin_setup", {"code": code, "site_url": settings.SITE_URL})
    notification = Notification.objects.create(
        recipient=recipient,
        to_e164=user.phone_e164,
        channel=user.preferred_channel,
        kind=NotificationKind.PIN_SETUP,
        body=body,
        dedupe_key=f"pin-setup:{token.id}",
        status=NotificationStatus.QUEUED,
        send_after=timezone.now(),
    )
    send_now(notification)
    return token
