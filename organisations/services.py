"""Membership lifecycle (invite, reset PIN) and the roster (docs/01-
requirements.md X.3, F1.5; docs/04-design.md §4.1). Views stay thin
(CLAUDE.md "Code style") and call these.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.utils.translation import gettext as _

from accounts.models import User
from accounts.phone import normalise
from accounts.services import issue_pin_setup_code
from organisations.models import (
    AuditEvent,
    Location,
    Membership,
    MembershipRole,
    Shift,
    ShiftAssignment,
)
from organisations.tenancy import tenant_context


class InviteError(Exception):
    """Raised with a user-facing message when an invite or reset can't proceed."""


def invite_member(*, actor: Membership, phone: str, name: str, role: str) -> Membership:
    """Creates the Membership (and the User, if this phone is new to
    WorkFlow entirely) and sends a PIN setup code. An existing phone number
    is reused as-is, which is what lets one person join a second
    organisation (docs/01-requirements.md X.4).

    Establishes its own tenant_context from `actor.organisation`, so this
    is safe to call from anywhere (a view already inside one, a shell, a
    future Celery task) rather than assuming the caller set one up."""
    if role == MembershipRole.OWNER and actor.role != MembershipRole.OWNER:
        raise InviteError(_("Only an Owner can add another Owner."))

    phone_e164 = normalise(phone)  # raises ValueError for the view to surface

    with tenant_context(actor.organisation):
        user = User.objects.filter(phone_e164=phone_e164).first()
        if user is None:
            user = User(phone_e164=phone_e164, name=name, must_set_pin=True)
            user.set_unusable_password()
            user.save()

        if Membership.objects.filter(user=user).exists():
            raise InviteError(_("This person is already a member of this organisation."))

        membership = Membership.objects.create(user=user, role=role)

        issue_pin_setup_code(user=user, created_by=actor.user, recipient=membership)

        AuditEvent.objects.create(
            actor=actor,
            action="membership.invite",
            target_type="membership",
            target_id=membership.id,
            changes={"role": [None, role]},
        )
        return membership


def reset_pin(*, actor: Membership, target: Membership) -> None:
    """A Supervisor may only reset a Staff member's PIN; Manager and Owner
    keep their full "locs"/"all" scope (docs/04-design.md §2). The base
    role/org checks belong to the caller (organisations.permissions.can);
    this enforces the Supervisor-target restriction that `can()` already
    applies too, so this function is safe to call on its own."""
    if actor.role == MembershipRole.SUPERVISOR and target.role != MembershipRole.STAFF:
        raise InviteError(_("Supervisors can only reset a PIN for Staff."))

    with tenant_context(actor.organisation):
        issue_pin_setup_code(user=target.user, created_by=actor.user, recipient=target)

        AuditEvent.objects.create(
            actor=actor,
            action="pin.reset",
            target_type="membership",
            target_id=target.id,
            changes={},
        )


# --- roster (docs/01-requirements.md F1.5) ---


def set_rostered(*, shift: Shift, membership: Membership, day: date, on: bool) -> None:
    """Adds or removes one roster entry. Idempotent either way."""
    with tenant_context(shift.organisation):
        if on:
            ShiftAssignment.objects.get_or_create(shift=shift, membership=membership, date=day)
        else:
            ShiftAssignment.objects.filter(shift=shift, membership=membership, date=day).delete()


def copy_previous_week(*, location: Location, week_start: date) -> int:
    """Copies last week's roster at `location` into the week starting
    `week_start`, skipping entries that already exist. Returns how many were
    added."""
    added = 0
    with tenant_context(location.organisation):
        previous = ShiftAssignment.objects.filter(
            shift__location=location,
            shift__is_active=True,
            membership__is_active=True,
            date__gte=week_start - timedelta(days=7),
            date__lt=week_start,
        ).select_related("shift", "membership")
        for entry in previous:
            _, created = ShiftAssignment.objects.get_or_create(
                shift=entry.shift, membership=entry.membership, date=entry.date + timedelta(days=7)
            )
            added += created
    return added


def _minutes(shift: Shift) -> tuple[int, int]:
    start = shift.start_time.hour * 60 + shift.start_time.minute
    end = shift.end_time.hour * 60 + shift.end_time.minute
    if end <= start:  # overnight: ends the next morning
        end += 24 * 60
    return start, end


def shifts_overlap(a: Shift, b: Shift) -> bool:
    a_start, a_end = _minutes(a)
    b_start, b_end = _minutes(b)
    return a_start < b_end and b_start < a_end


@dataclass(frozen=True)
class Overlap:
    membership: Membership
    day: date
    first: Shift
    second: Shift


def find_overlaps(assignments) -> list[Overlap]:
    """Same person, same day, on shifts whose times overlap — a warning on
    the roster, not an error (docs/01-requirements.md F1.5)."""
    by_person_day: dict[tuple, list[ShiftAssignment]] = {}
    for entry in assignments:
        by_person_day.setdefault((entry.membership_id, entry.date), []).append(entry)

    overlaps = []
    for entries in by_person_day.values():
        for i, first in enumerate(entries):
            for second in entries[i + 1 :]:
                if shifts_overlap(first.shift, second.shift):
                    overlaps.append(
                        Overlap(first.membership, first.date, first.shift, second.shift)
                    )
    return overlaps
