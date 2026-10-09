"""PinBackend: the only entry in AUTHENTICATION_BACKENDS (admin login uses it
too — it accepts `username`/`password` as aliases for `phone`/`pin`).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend
from django.http import HttpRequest
from django.utils import timezone

from accounts.phone import normalise

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


class PinBackend(BaseBackend):
    def authenticate(
        self,
        request: HttpRequest | None,
        username: str | None = None,
        password: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        pin: str | None = None,
        **kwargs: Any,
    ):
        raw_identifier = phone or email or username
        raw_pin = pin if pin is not None else password
        if not raw_identifier or not raw_pin:
            return None

        user_model = get_user_model()

        # Try to find user by email first if it looks like email
        if email or (username and "@" in str(username)):
            identifier = email or username
            try:
                user = user_model.objects.get(email=identifier)
            except user_model.DoesNotExist:
                return None
        else:
            # Try phone
            try:
                phone_e164 = normalise(raw_identifier)
            except ValueError:
                return None

            try:
                user = user_model.objects.get(phone_e164=phone_e164)
            except user_model.DoesNotExist:
                return None

        now = timezone.now()
        if user.locked_until and user.locked_until > now:
            return None

        if not user.check_password(raw_pin):
            user.failed_pin_attempts += 1
            if user.failed_pin_attempts >= MAX_FAILED_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.save(update_fields=["failed_pin_attempts", "locked_until"])
            return None

        if user.failed_pin_attempts or user.locked_until:
            user.failed_pin_attempts = 0
            user.locked_until = None
            user.save(update_fields=["failed_pin_attempts", "locked_until"])

        return user

    def get_user(self, user_id: str):
        user_model = get_user_model()
        try:
            return user_model.objects.get(pk=user_id)
        except user_model.DoesNotExist:
            return None
