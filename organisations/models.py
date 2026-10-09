"""Organisation, Location, Membership, Shift, ShiftAssignment, AuditEvent.

Field lists, constraints and indexes follow docs/04-design.md §1.2 exactly.
`Organisation` is global (it *is* the tenant); everything else here is
tenant-scoped (docs/04-design.md §1.1).
"""

from __future__ import annotations

import zoneinfo
from datetime import time
from uuid import uuid4

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from organisations.tenancy import SyncedTenantModel, TenantModel


def validate_timezone(value: str) -> None:
    if value not in zoneinfo.available_timezones():
        raise ValidationError(f"{value!r} is not a known IANA timezone.")


class Organisation(models.Model):
    """Global: not tenant-scoped. This model *is* the tenant."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    timezone = models.CharField(
        max_length=64, default="Africa/Kampala", validators=[validate_timezone]
    )
    country_code = models.CharField(max_length=2, default="UG")
    is_active = models.BooleanField(default=True)
    photo_required_default = models.BooleanField(default=True)
    default_reminder_lead_min = models.PositiveSmallIntegerField(default=30)
    escalation_delay_min = models.PositiveSmallIntegerField(default=30)
    # time objects, not "22:00" strings: Django doesn't convert a default on
    # save, so a string default leaves a fresh instance holding a str.
    quiet_hours_start = models.TimeField(default=time(22, 0))
    quiet_hours_end = models.TimeField(default=time(6, 0))
    sms_monthly_cap = models.PositiveIntegerField(default=1000)
    daily_summary_time = models.TimeField(null=True, blank=True)
    photo_retention_days = models.PositiveSmallIntegerField(default=90)
    record_retention_days = models.PositiveSmallIntegerField(default=730)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Location(SyncedTenantModel):
    name = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta(SyncedTenantModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["organisation", "name"], name="uniq_location_org_name"),
        ]
        indexes = [
            models.Index(fields=["organisation", "updated_seq"], name="idx_location_org_seq"),
        ]
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name


class MembershipRole(models.TextChoices):
    OWNER = "owner", "Owner"
    MANAGER = "manager", "Manager"
    SUPERVISOR = "supervisor", "Supervisor"
    STAFF = "staff", "Staff"


class Membership(SyncedTenantModel):
    """A person's role within one organisation. Also doubles as the "people"
    projection synced to phones (id, name, role)."""

    user = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="memberships")
    role = models.CharField(max_length=20, choices=MembershipRole.choices)
    is_active = models.BooleanField(default=True)
    locations = models.ManyToManyField(
        Location, through="MembershipLocation", related_name="memberships", blank=True
    )
    display_name = models.CharField(max_length=80, blank=True)

    class Meta(SyncedTenantModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "user"], name="uniq_membership_org_user"
            ),
        ]
        indexes = [
            models.Index(fields=["organisation", "role"], name="idx_membership_org_role"),
            models.Index(fields=["organisation", "updated_seq"], name="idx_membership_org_seq"),
        ]

    def __str__(self) -> str:
        return self.display_name or self.user.name

    @property
    def name(self) -> str:
        return self.display_name or self.user.name


class MembershipLocation(TenantModel):
    """Explicit through model for Membership.locations (tenant-scoped)."""

    membership = models.ForeignKey(Membership, on_delete=models.CASCADE)
    location = models.ForeignKey(Location, on_delete=models.CASCADE)

    class Meta(TenantModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "location"], name="uniq_membershiplocation"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.membership} @ {self.location}"


class Shift(SyncedTenantModel):
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="shifts")
    name = models.CharField(max_length=60)
    start_time = models.TimeField()
    end_time = models.TimeField()
    weekdays = ArrayField(models.PositiveSmallIntegerField(), help_text="0 = Mon ... 6 = Sun")
    is_active = models.BooleanField(default=True)

    class Meta(SyncedTenantModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "location", "name"], name="uniq_shift_org_loc_name"
            ),
        ]
        indexes = [
            models.Index(fields=["organisation", "updated_seq"], name="idx_shift_org_seq"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.location})"


class ShiftAssignment(SyncedTenantModel):
    """A roster entry: `membership` works `shift` on `date`."""

    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="assignments")
    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, related_name="shift_assignments"
    )
    date = models.DateField(help_text="The local date the shift starts.")

    class Meta(SyncedTenantModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["shift", "membership", "date"], name="uniq_shiftassignment"
            ),
        ]
        indexes = [
            models.Index(fields=["organisation", "date"], name="idx_shiftassign_org_date"),
            models.Index(
                fields=["organisation", "membership", "date"],
                name="idx_shiftassign_org_mem_date",
            ),
            models.Index(fields=["organisation", "updated_seq"], name="idx_shiftassign_org_seq"),
        ]

    def __str__(self) -> str:
        return f"{self.membership} / {self.shift} on {self.date}"


class AuditEvent(TenantModel):
    """Not synced. `actor=None` means a system action."""

    actor = models.ForeignKey(
        Membership, on_delete=models.SET_NULL, null=True, related_name="audit_events"
    )
    action = models.CharField(max_length=40)
    target_type = models.CharField(max_length=40)
    target_id = models.UUIDField()
    changes = models.JSONField(default=dict, blank=True)

    class Meta(TenantModel.Meta):
        indexes = [
            models.Index(fields=["organisation", "created_at"], name="idx_audit_org_created"),
            models.Index(
                fields=["organisation", "target_type", "target_id"], name="idx_audit_org_target"
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.action} on {self.target_type}:{self.target_id}"


class MemberActivity(TenantModel):
    """When a member was last active, and when they last opened the notification
    list (ADR-21). Deliberately not on Membership: that row is synced, and every
    update bumps `updated_seq`, so phones would re-pull it every minute."""

    membership = models.OneToOneField(Membership, on_delete=models.CASCADE, related_name="activity")
    last_seen_at = models.DateTimeField(null=True, blank=True)
    notifications_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantModel.Meta):
        verbose_name_plural = "member activity"

    def save(self, *args, **kwargs) -> None:
        if self.organisation_id and self.membership.organisation_id != self.organisation_id:
            raise ValueError("MemberActivity and its membership must share an organisation.")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.membership} seen {self.last_seen_at}"
