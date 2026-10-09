"""Member activity: "last seen" for the People cards and "notifications seen"
for the header bell (ADR-21). Presence is derived, never stored."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from organisations.models import MemberActivity, Membership

TOUCH_EVERY = timedelta(minutes=1)
ONLINE_WITHIN = timedelta(minutes=5)
AWAY_WITHIN = timedelta(hours=1)


def touch(membership: Membership, now: datetime | None = None) -> None:
    """Record that `membership` just used WorkFlow. Callers throttle this
    (the middleware does it once a minute per session)."""
    now = now or timezone.now()
    updated = MemberActivity.objects.filter(membership=membership).update(last_seen_at=now)
    if not updated:
        MemberActivity.objects.get_or_create(
            membership=membership,
            defaults={"organisation_id": membership.organisation_id, "last_seen_at": now},
        )


def mark_notifications_seen(membership: Membership, now: datetime | None = None) -> None:
    now = now or timezone.now()
    activity, created = MemberActivity.objects.get_or_create(
        membership=membership,
        defaults={"organisation_id": membership.organisation_id, "notifications_seen_at": now},
    )
    if not created:
        activity.notifications_seen_at = now
        activity.save(update_fields=["notifications_seen_at", "updated_at"])


def presence(last_seen_at: datetime | None, now: datetime | None = None) -> str:
    """'online' (≤ 5 min), 'away' (≤ 1 h) or 'offline'."""
    if last_seen_at is None:
        return "offline"
    age = (now or timezone.now()) - last_seen_at
    if age <= ONLINE_WITHIN:
        return "online"
    if age <= AWAY_WITHIN:
        return "away"
    return "offline"


def annotate_people(memberships: list[Membership], now: datetime | None = None) -> None:
    """Adds `last_seen_at`, `presence`, `location_names` and `current_task`
    (the newest task they started and haven't finished) to each membership,
    for the People cards (X.5). Three queries whatever the list size."""
    from tasks.models import Task, TaskStatus  # tasks depends on organisations

    now = now or timezone.now()
    ids = [m.id for m in memberships]
    seen = dict(
        MemberActivity.objects.filter(membership_id__in=ids).values_list(
            "membership_id", "last_seen_at"
        )
    )
    current: dict = {}
    for task in (
        Task.objects.filter(started_by_id__in=ids, status=TaskStatus.IN_PROGRESS)
        .only("id", "title", "started_by_id")
        .order_by("-updated_at")
    ):
        current.setdefault(task.started_by_id, task)
    for m in memberships:
        m.last_seen_at = seen.get(m.id)
        m.presence = presence(m.last_seen_at, now)
        m.location_names = [loc.name for loc in m.locations.all()]
        m.current_task = current.get(m.id)
