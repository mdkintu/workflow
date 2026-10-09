"""S1 Login and S2 PIN setup. The rest of §4.1's accounts routes are later
feature work.
"""

from __future__ import annotations

from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.hashers import check_password
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.models import PinSetupToken, User
from accounts.phone import normalise
from accounts.pin import validate_pin
from accounts.ratelimit import is_ip_rate_limited, record_login_attempt

_GENERIC_CODE_ERROR = _("That code is invalid or has expired.")


def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")

    error = None
    if request.method == "POST":
        if is_ip_rate_limited(request, scope="login"):
            return render(
                request,
                "accounts/login.html",
                {"error": _("Too many attempts. Try again in a few minutes.")},
                status=429,
            )
        record_login_attempt(request, scope="login")

        phone = request.POST.get("phone", "")
        pin = request.POST.get("pin", "")
        user = authenticate(request, phone=phone, pin=pin)
        if user is not None:
            auth_login(request, user)
            return redirect("home")
        error = _("Wrong phone number or PIN.")

    return render(request, "accounts/login.html", {"error": error})


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    """Logging out clears this phone's copy of the work too (docs/01 Q7:
    phones may be shared): the browser drops the service worker caches and
    the field app's local database."""
    auth_logout(request)
    response = redirect("accounts:login")
    response["Clear-Site-Data"] = '"cache", "storage"'
    return response


def pin_setup_view(request: HttpRequest) -> HttpResponse:
    """First-time setup after an invite, or a manager/supervisor-triggered
    reset — both just issue a PinSetupToken (accounts.services), so this one
    public, rate-limited view handles either."""
    if request.user.is_authenticated:
        return redirect("home")

    error = None
    if request.method == "POST":
        if is_ip_rate_limited(request, scope="pin-setup"):
            return render(
                request,
                "accounts/pin_setup.html",
                {"error": _("Too many attempts. Try again in a few minutes.")},
                status=429,
            )
        record_login_attempt(request, scope="pin-setup")

        phone = request.POST.get("phone", "")
        code = request.POST.get("code", "")
        pin = request.POST.get("pin", "")
        pin_confirm = request.POST.get("pin_confirm", "")

        user, token = _find_valid_token(phone, code)
        if user is None or token is None:
            error = _GENERIC_CODE_ERROR
        elif pin != pin_confirm:
            error = _("The two PINs don't match.")
        else:
            error = validate_pin(pin, user)
            if error is None:
                _complete_pin_setup(user, token, pin)
                auth_login(request, user, backend="accounts.backends.PinBackend")
                return redirect("home")

    return render(request, "accounts/pin_setup.html", {"error": error})


def _find_valid_token(raw_phone: str, code: str) -> tuple[User | None, PinSetupToken | None]:
    try:
        phone_e164 = normalise(raw_phone)
        user = User.objects.get(phone_e164=phone_e164)
    except (ValueError, User.DoesNotExist):
        return None, None

    token = (
        PinSetupToken.objects.filter(user=user, used_at__isnull=True, expires_at__gt=timezone.now())
        .order_by("-expires_at")
        .first()
    )
    if token is None or not check_password(code, token.code_hash):
        return None, None
    return user, token


def _complete_pin_setup(user: User, token: PinSetupToken, pin: str) -> None:
    user.set_password(pin)
    user.must_set_pin = False
    user.failed_pin_attempts = 0
    user.locked_until = None
    user.save(update_fields=["password", "must_set_pin", "failed_pin_attempts", "locked_until"])
    token.used_at = timezone.now()
    token.save(update_fields=["used_at"])
