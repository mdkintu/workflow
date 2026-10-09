"""accounts.User (global, phone + PIN) and accounts.PinSetupToken.

See docs/04-design.md §1.2 and ADR-06/ADR-07.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models

from accounts.phone import normalise


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, phone_e164: str, password: str, name: str | None, **extra_fields: Any):
        if not phone_e164:
            raise ValueError("Users must have a phone number.")
        user = self.model(phone_e164=phone_e164, name=name or "", **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, phone: str, pin: str, name: str | None = None, **extra_fields: Any):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create(normalise(phone), pin, name, **extra_fields)

    def create_superuser(
        self,
        phone_e164: str | None = None,
        password: str | None = None,
        name: str | None = None,
        **extra_fields: Any,
    ):
        # Parameter names match USERNAME_FIELD/REQUIRED_FIELDS so Django's
        # `createsuperuser` management command can call this directly.
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("must_set_pin", False)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(phone_e164, password, name, **extra_fields)


class PreferredChannel(models.TextChoices):
    SMS = "sms", "SMS"
    WHATSAPP = "whatsapp", "WhatsApp"


class User(AbstractBaseUser, PermissionsMixin):
    """Global (not tenant-scoped): a phone number is one person, and one
    person may hold Memberships in several organisations."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    phone_e164 = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=80, blank=True)
    must_set_pin = models.BooleanField(default=True)
    failed_pin_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    preferred_channel = models.CharField(
        max_length=10, choices=PreferredChannel.choices, default=PreferredChannel.SMS
    )
    reminders_opt_out = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "phone_e164"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name or self.phone_e164


class PinSetupToken(models.Model):
    """One-time SMS code for first PIN setup or a manager-triggered reset."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="pin_setup_tokens")
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        indexes = [
            models.Index(fields=["user", "expires_at"], name="idx_pinsetuptoken_user_exp"),
        ]

    def __str__(self) -> str:
        return f"PIN setup token for {self.user}"
