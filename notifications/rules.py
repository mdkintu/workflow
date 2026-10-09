"""Who gets alerted, and when (docs/01-requirements.md F5, docs/02-
architecture.md §5-6). Plain functions over the tenant-scoped managers, so
callers run them inside a tenant_context.
"""

from __future__ import annotations

import zoneinfo
from datetime import date, datetime, timedelta

from django.db.models import Q

from organisations.models import (
    Location,
    Membership,
    MembershipLocation,
    MembershipRole,
    Organisation,
    ShiftAssignment,
)

MAX_BACKOFF = timedelta(hours=1)


def _tz(organisation: Organisation) -> zoneinfo.ZoneInfo:
    return zoneinfo.ZoneInfo(organisation.timezone)


def in_quiet_hours(organisation: Organisation, when: datetime) -> bool:
    """Quiet hours may wrap past midnight (the default is 22:00-06:00)."""
    start, end = organisation.quiet_hours_start, organisation.quiet_hours_end
    local = when.astimezone(_tz(organisation)).time()
    if start == end:
        return False
    if start < end:
        return start <= local < end
    return local >= start or local < end


def quiet_end(organisation: Organisation, when: datetime) -> datetime:
    """The next moment quiet hours are over."""
    tz = _tz(organisation)
    local = when.astimezone(tz)
    end = datetime.combine(local.date(), organisation.quiet_hours_end, tzinfo=tz)
    return end if end > local else end + timedelta(days=1)


def backoff(attempts: int) -> timedelta:
    """1, 2, 4, 8 ... minutes after each failed attempt, at most an hour."""
    return min(timedelta(minutes=2 ** max(attempts - 1, 0)), MAX_BACKOFF)


def _covers(entry: ShiftAssignment, when: datetime, tz: zoneinfo.ZoneInfo) -> bool:
    start = datetime.combine(entry.date, entry.shift.start_time, tzinfo=tz)
    end = datetime.combine(entry.date, entry.shift.end_time, tzinfo=tz)
    if end <= start:  # overnight
        end += timedelta(days=1)
    return start <= when < end


def _days_that_could_cover(when: datetime, tz: zoneinfo.ZoneInfo) -> list[date]:
    today = when.astimezone(tz).date()
    return [today - timedelta(days=1), today]  # yesterday: an overnight shift


def is_on_shift(membership: Membership, when: datetime) -> bool:
    tz = _tz(membership.organisation)
    entries = ShiftAssignment.objects.filter(
        membership=membership, date__in=_days_that_could_cover(when, tz), shift__is_active=True
    ).select_related("shift")
    return any(_covers(entry, when, tz) for entry in entries)


def managers_for(location: Location) -> list[Membership]:
    """Managers linked to this location, or linked to none (= all
    locations, docs/04-design.md §2). With no managers at all, the owners."""
    linked_here = MembershipLocation.objects.filter(location=location).values("membership_id")
    linked_anywhere = MembershipLocation.objects.values("membership_id")
    managers = list(
        Membership.objects.filter(role=MembershipRole.MANAGER, is_active=True)
        .filter(Q(id__in=linked_here) | ~Q(id__in=linked_anywhere))
        .select_related("user")
    )
    if managers:
        return managers
    return list(
        Membership.objects.filter(role=MembershipRole.OWNER, is_active=True).select_related("user")
    )


def supervisors_on_duty(location: Location, when: datetime) -> list[Membership]:
    """Supervisors rostered at this location right now; else those linked to
    it; else every supervisor; else the location's managers."""
    tz = _tz(location.organisation)
    entries = ShiftAssignment.objects.filter(
        shift__location=location,
        shift__is_active=True,
        date__in=_days_that_could_cover(when, tz),
        membership__role=MembershipRole.SUPERVISOR,
        membership__is_active=True,
    ).select_related("shift", "membership__user")
    on_duty = list(
        {e.membership_id: e.membership for e in entries if _covers(e, when, tz)}.values()
    )
    if on_duty:
        return on_duty

    supervisors = Membership.objects.filter(
        role=MembershipRole.SUPERVISOR, is_active=True
    ).select_related("user")
    linked = list(
        supervisors.filter(
            id__in=MembershipLocation.objects.filter(location=location).values("membership_id")
        )
    )
    if linked:
        return linked
    everyone = list(supervisors)
    return everyone or managers_for(location)
