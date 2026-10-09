"""S3 Organisation picker, people (invite/reset PIN), locations, shifts and
the weekly roster (docs/04-design.md §3 S9, §4.1). Organisation settings are
later feature work.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from organisations.activity import annotate_people
from organisations.forms import WEEKDAY_CHOICES, LocationForm, ShiftForm
from organisations.models import Location, Membership, MembershipRole, Shift, ShiftAssignment
from organisations.permissions import Role, can, role_required
from organisations.services import (
    InviteError,
    copy_previous_week,
    find_overlaps,
    invite_member,
    reset_pin,
    set_rostered,
)


@login_required
def org_switch(request: HttpRequest) -> HttpResponse:
    # `.unscoped` is required, not a shortcut: this is the one screen that
    # must list a user's memberships *across* organisations, by definition
    # before any single one is "current" (this path is also exempt from
    # TenantMiddleware — see organisations/middleware.py's EXEMPT_PREFIXES).
    memberships = Membership.unscoped.select_related("organisation").filter(
        user=request.user, is_active=True
    )

    if request.method == "POST":
        membership_id = request.POST.get("membership_id")
        membership = memberships.filter(id=membership_id).first()
        if membership is not None:
            request.session["active_membership_id"] = str(membership.id)
            return redirect("home")

    return render(request, "organisations/org_switch.html", {"memberships": memberships})


@login_required
@role_required(Role.MANAGER)
def people_list(request: HttpRequest) -> HttpResponse:
    memberships = list(
        Membership.objects.select_related("user")
        .prefetch_related("locations")
        .filter(is_active=True)
        .order_by("user__name")
    )
    annotate_people(memberships)
    # Django templates can't call a function with an argument, so the
    # per-row permission check is precomputed here rather than in the
    # template (docs/04-design.md §2: Supervisor's pin.reset scope is
    # narrower than Manager/Owner's).
    for membership in memberships:
        membership.can_reset_pin = can(request.membership, "pin.reset", obj=membership)

    return render(request, "organisations/people.html", {"memberships": memberships})


@login_required
@role_required(Role.MANAGER)
def people_invite(request: HttpRequest) -> HttpResponse:
    error = None
    role_choices = [
        (value, label)
        for value, label in MembershipRole.choices
        if value != MembershipRole.OWNER or request.membership.role == MembershipRole.OWNER
    ]

    if request.method == "POST":
        phone = request.POST.get("phone", "")
        name = request.POST.get("name", "").strip()
        role = request.POST.get("role", "")

        if not name:
            error = _("Name is required.")
        elif role not in dict(role_choices):
            error = _("Choose a role.")
        else:
            try:
                membership = invite_member(
                    actor=request.membership, phone=phone, name=name, role=role
                )
            except (ValueError, InviteError) as exc:
                error = str(exc)
            else:
                messages.success(request, _("Invited %(name)s.") % {"name": membership.name})
                return redirect("organisations:people")

    return render(
        request,
        "organisations/people_invite.html",
        {"error": error, "role_choices": role_choices},
    )


@login_required
@role_required(Role.SUPERVISOR)
@require_POST
def people_reset_pin(request: HttpRequest, membership_id: str) -> HttpResponse:
    target = get_object_or_404(Membership, id=membership_id, is_active=True)
    if not can(request.membership, "pin.reset", obj=target):
        raise PermissionDenied

    reset_pin(actor=request.membership, target=target)
    messages.success(request, _("Sent a new PIN setup code to %(name)s.") % {"name": target.name})
    return redirect("organisations:people")


# --- locations and shifts (Manager+) ---


@login_required
@role_required(Role.MANAGER)
def locations(request: HttpRequest) -> HttpResponse:
    form = LocationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        Location.objects.create(name=form.cleaned_data["name"])
        messages.success(request, _("Location added."))
        return redirect("organisations:locations")
    return render(
        request,
        "organisations/locations.html",
        {"form": form, "locations": Location.objects.all()},
    )


@login_required
@role_required(Role.MANAGER)
def shifts(request: HttpRequest) -> HttpResponse:
    form = ShiftForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        Shift.objects.create(**form.cleaned_data)
        messages.success(request, _("Shift added."))
        return redirect("organisations:shifts")
    day_names = dict(WEEKDAY_CHOICES)
    all_shifts = list(
        Shift.objects.filter(is_active=True)
        .select_related("location")
        .order_by("location__sort_order", "location__name", "start_time")
    )
    for shift in all_shifts:
        shift.day_names = [day_names[d] for d in shift.weekdays]
    return render(request, "organisations/shifts.html", {"form": form, "shifts": all_shifts})


# --- roster (S9): Supervisor+ can view, Manager+ can edit ---


def _week_start(raw: str) -> date:
    """ISO week like "2026-W40" -> its Monday; anything else -> this week."""
    try:
        return datetime.strptime(f"{raw}-1", "%G-W%V-%u").date()
    except ValueError:
        today = timezone.localdate()
        return today - timedelta(days=today.weekday())


def _pick_location(raw: str) -> Location | None:
    active = Location.objects.filter(is_active=True)
    try:
        return active.filter(id=UUID(raw)).first() or active.first()
    except ValueError:
        return active.first()


def _get_or_404(model, raw_id, **filters):
    try:
        return get_object_or_404(model, pk=UUID(str(raw_id)), **filters)
    except ValueError as exc:
        raise Http404 from exc


def _people():
    return sorted(
        Membership.objects.filter(is_active=True).select_related("user"), key=lambda p: p.name
    )


def _cell(shift: Shift, day: date, assigned: list[Membership], people, can_edit: bool) -> dict:
    assigned_ids = {person.id for person in assigned}
    return {
        "shift": shift,
        "day": day,
        "assigned": sorted(assigned, key=lambda p: p.name),
        "addable": [person for person in people if person.id not in assigned_ids],
        "can_edit": can_edit,
    }


@login_required
@role_required(Role.SUPERVISOR)
def roster(request: HttpRequest) -> HttpResponse:
    week_start = _week_start(request.GET.get("week", ""))
    days = [week_start + timedelta(days=i) for i in range(7)]
    location = _pick_location(request.GET.get("loc", ""))
    can_edit = can(request.membership, "roster.edit")
    people = _people()

    rows, overlaps = [], []
    if location is not None:
        location_shifts = list(
            Shift.objects.filter(location=location, is_active=True).order_by("start_time")
        )
        # One query for the whole week, not one per cell.
        assigned: dict[tuple, list[Membership]] = {}
        for entry in ShiftAssignment.objects.filter(
            shift__in=location_shifts, date__range=(days[0], days[-1])
        ).select_related("membership__user"):
            assigned.setdefault((entry.shift_id, entry.date), []).append(entry.membership)
        rows = [
            {
                "shift": shift,
                "cells": [
                    _cell(shift, day, assigned.get((shift.id, day), []), people, can_edit)
                    for day in days
                ],
            }
            for shift in location_shifts
        ]
        rostered_here = ShiftAssignment.objects.filter(
            shift__location=location, date__range=(days[0], days[-1])
        ).values("membership_id")
        # Overlaps are checked across every location, for the people on this grid.
        overlaps = find_overlaps(
            ShiftAssignment.objects.filter(
                membership_id__in=rostered_here, date__range=(days[0], days[-1])
            ).select_related("shift", "membership__user")
        )

    return render(
        request,
        "organisations/roster.html",
        {
            "location": location,
            "locations": Location.objects.filter(is_active=True),
            "days": days,
            "rows": rows,
            "overlaps": overlaps,
            "can_edit": can_edit,
            "week": week_start.strftime("%G-W%V"),
            "previous_week": (week_start - timedelta(days=7)).strftime("%G-W%V"),
            "next_week": (week_start + timedelta(days=7)).strftime("%G-W%V"),
        },
    )


@login_required
@role_required(Role.MANAGER)
@require_POST
def roster_cell(request: HttpRequest) -> HttpResponse:
    """HTMX: add or remove one person on one shift-day, then re-render just
    that cell."""
    if not can(request.membership, "roster.edit"):
        raise PermissionDenied
    shift = _get_or_404(Shift, request.POST.get("shift"), is_active=True)
    membership = _get_or_404(Membership, request.POST.get("membership"), is_active=True)
    try:
        day = date.fromisoformat(request.POST.get("date", ""))
    except ValueError as exc:
        raise Http404 from exc

    set_rostered(shift=shift, membership=membership, day=day, on=request.POST.get("op") == "add")
    assigned = [
        entry.membership
        for entry in ShiftAssignment.objects.filter(shift=shift, date=day).select_related(
            "membership__user"
        )
    ]
    cell = _cell(shift, day, assigned, _people(), can_edit=True)
    return render(request, "organisations/_roster_cell.html", {"cell": cell})


@login_required
@role_required(Role.MANAGER)
@require_POST
def roster_copy_week(request: HttpRequest) -> HttpResponse:
    location = _get_or_404(Location, request.POST.get("loc"), is_active=True)
    week_start = _week_start(request.POST.get("week", ""))
    added = copy_previous_week(location=location, week_start=week_start)
    messages.success(request, _("Copied %(n)d entries from last week.") % {"n": added})
    return redirect(f"{reverse('roster:roster')}?loc={location.id}&week={week_start:%G-W%V}")
