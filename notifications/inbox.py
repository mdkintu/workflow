"""The header bell's list (F5.6, ADR-21): a member's own recent notifications.
Read-only: SMS/WhatsApp delivery is unchanged, and nothing here sends anything."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db.models import F, Q, Subquery
from django.urls import reverse
from django.utils import timezone

from notifications.models import Notification, NotificationKind, NotificationStatus
from organisations.models import MemberActivity, Membership

WINDOW = timedelta(days=7)
LIMIT = 20

ICONS = {
    NotificationKind.REMINDER: "clock",
    NotificationKind.OVERDUE: "triangle-alert",
    NotificationKind.ESCALATION: "triangle-alert",
    NotificationKind.FLAG: "flag",
    NotificationKind.REJECTED: "rotate-ccw",
}
TARGET_URLS = {"task": "tasks:detail", "run": "checklists:run"}


def _recent(membership: Membership, now: datetime):
    return Notification.objects.filter(recipient=membership, created_at__gte=now - WINDOW).exclude(
        status=NotificationStatus.SUPPRESSED
    )


def _seen_at(membership: Membership) -> datetime | None:
    return (
        MemberActivity.objects.filter(membership=membership)
        .values_list("notifications_seen_at", flat=True)
        .first()
    )


def has_unseen(membership: Membership, now: datetime | None = None) -> bool:
    """One query: it runs on every page with the header (the bell's red dot)."""
    now = now or timezone.now()
    seen_at = MemberActivity.objects.filter(membership=membership).values("notifications_seen_at")[
        :1
    ]
    return (
        _recent(membership, now)
        .annotate(seen_at=Subquery(seen_at))
        .filter(Q(seen_at__isnull=True) | Q(created_at__gt=F("seen_at")))
        .exists()
    )


def recent_items(membership: Membership, now: datetime | None = None) -> list[Notification]:
    """Newest first, each with `icon`, `url` (or None) and `is_new`."""
    now = now or timezone.now()
    seen_at = _seen_at(membership)
    items = list(_recent(membership, now).order_by("-created_at")[:LIMIT])
    for item in items:
        item.icon = ICONS.get(item.kind, "bell")
        name = TARGET_URLS.get(item.target_type)
        item.url = reverse(name, args=[item.target_id]) if name and item.target_id else None
        item.is_new = seen_at is None or item.created_at > seen_at
    return items
