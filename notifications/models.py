"""Notification (intent + delivery log) and SmsUsage. See
docs/04-design.md §1.2 and §6 (dispatch pipeline, provider interface).
"""

from __future__ import annotations

from django.db import models

from organisations.tenancy import TenantModel


class NotificationChannel(models.TextChoices):
    SMS = "sms", "SMS"
    WHATSAPP = "whatsapp", "WhatsApp"


class NotificationKind(models.TextChoices):
    REMINDER = "reminder", "Reminder"
    OVERDUE = "overdue", "Overdue"
    ESCALATION = "escalation", "Escalation"
    FLAG = "flag", "Flag"
    REJECTED = "rejected", "Rejected"
    SYNCED_LATE = "synced_late", "Synced late"
    DAILY_SUMMARY = "daily_summary", "Daily summary"
    PIN_SETUP = "pin_setup", "PIN setup"
    BUDGET_WARNING = "budget_warning", "SMS budget warning"


class NotificationStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    HELD = "held", "Held"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    SUPPRESSED = "suppressed", "Suppressed"


class Notification(TenantModel):
    recipient = models.ForeignKey(
        "organisations.Membership", on_delete=models.CASCADE, related_name="notifications"
    )
    to_e164 = models.CharField(max_length=16)
    channel = models.CharField(max_length=10, choices=NotificationChannel.choices)
    kind = models.CharField(max_length=20, choices=NotificationKind.choices)
    target_type = models.CharField(max_length=20, blank=True)
    target_id = models.UUIDField(null=True, blank=True)
    step = models.PositiveSmallIntegerField(default=0)
    body = models.CharField(max_length=160)
    dedupe_key = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=20, choices=NotificationStatus.choices)
    send_after = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    provider_message_id = models.CharField(max_length=80, blank=True)
    cost = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantModel.Meta):
        indexes = [
            # No organisation prefix: the dispatcher scans across every
            # organisation using the `unscoped` manager (docs/02 §5).
            models.Index(fields=["status", "send_after"], name="idx_notification_status_send"),
            models.Index(
                fields=["organisation", "created_at"], name="idx_notification_org_created"
            ),
            models.Index(
                fields=["organisation", "target_type", "target_id"],
                name="idx_notification_org_target",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} -> {self.to_e164} ({self.status})"


class SmsUsage(TenantModel):
    month = models.DateField(help_text="First day of the month.")
    count = models.PositiveIntegerField(default=0)
    warned_80 = models.BooleanField(default=False)

    class Meta(TenantModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "month"], name="uniq_smsusage_org_month"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.organisation_id} {self.month}: {self.count}"
