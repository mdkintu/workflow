"""OfflineSyncLog: the idempotency record for /api/sync/push. Not synced to
phones. `id` is the client's `mutation_id` — it has no default, unlike every
other TenantModel, because the client generates it (docs/04-design.md §1.2,
§4.5).
"""

from __future__ import annotations

from django.db import models

from organisations.tenancy import TenantModel


class SyncResultStatus(models.TextChoices):
    APPLIED = "applied", "Applied"
    REJECTED = "rejected", "Rejected"


class OfflineSyncLog(TenantModel):
    id = models.UUIDField(primary_key=True, editable=False)
    membership = models.ForeignKey(
        "organisations.Membership", on_delete=models.CASCADE, related_name="sync_logs"
    )
    device_id = models.UUIDField()
    kind = models.CharField(max_length=30)
    device_time = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    result_status = models.CharField(max_length=20, choices=SyncResultStatus.choices)
    result_code = models.CharField(max_length=40, blank=True)
    result_json = models.JSONField()

    class Meta(TenantModel.Meta):
        indexes = [
            models.Index(fields=["organisation", "received_at"], name="idx_syncoplog_org_received"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} ({self.result_status})"
