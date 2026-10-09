"""Locations, shifts and the weekly roster (docs/01-requirements.md F1.5,
docs/04-design.md §3 S9, §4.1 /org/locations/, /org/shifts/, /roster/)."""

from datetime import time, timedelta

import pytest
from django.utils import timezone

from organisations.models import Location, Shift, ShiftAssignment
from tests.factories import (
    LocationFactory,
    MembershipFactory,
    OrganisationFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
)

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def _monday(day=None):
    day = day or timezone.localdate()
    return day - timedelta(days=day.weekday())


# --- locations / shifts ---


def test_a_manager_adds_a_location(org_a, manager_membership, login_as):
    response = login_as(manager_membership).post("/org/locations/", {"name": "Restaurant"})
    assert response.status_code == 302
    assert Location.unscoped.filter(organisation=org_a, name="Restaurant").exists()


def test_a_duplicate_location_name_is_rejected(org_a, manager_membership, login_as):
    LocationFactory(organisation=org_a, name="Restaurant")
    response = login_as(manager_membership).post("/org/locations/", {"name": "Restaurant"})
    assert response.status_code == 200
    assert Location.unscoped.filter(organisation=org_a, name="Restaurant").count() == 1


def test_a_supervisor_cannot_manage_locations(org_a, login_as):
    supervisor = MembershipFactory(organisation=org_a, role="supervisor")
    assert login_as(supervisor).get("/org/locations/").status_code == 403


def test_a_manager_adds_a_shift(org_a, manager_membership, login_as):
    location = LocationFactory(organisation=org_a)
    response = login_as(manager_membership).post(
        "/org/shifts/",
        {
            "location": str(location.id),
            "name": "Night",
            "start_time": "22:00",
            "end_time": "06:00",
            "weekdays": ["0", "1", "2", "3", "4", "5", "6"],
        },
    )
    assert response.status_code == 302
    shift = Shift.unscoped.get(organisation=org_a, name="Night")
    assert shift.start_time == time(22, 0)
    assert shift.weekdays == [0, 1, 2, 3, 4, 5, 6]


def test_a_shift_at_another_organisations_location_is_rejected(org_a, manager_membership, login_as):
    foreign = LocationFactory(organisation=OrganisationFactory())
    login_as(manager_membership).post(
        "/org/shifts/",
        {
            "location": str(foreign.id),
            "name": "Night",
            "start_time": "22:00",
            "end_time": "06:00",
            "weekdays": ["0"],
        },
    )
    assert not Shift.unscoped.filter(name="Night").exists()


# --- roster ---


@pytest.fixture
def roster(org_a, manager_membership):
    location = LocationFactory(organisation=org_a, name="Main building")
    shift = ShiftFactory(organisation=org_a, location=location, name="Morning")
    staff = MembershipFactory(organisation=org_a, role="staff", user__name="Peter Okello")
    return location, shift, staff


def test_the_roster_shows_who_works_which_shift(org_a, manager_membership, roster, login_as):
    location, shift, staff = roster
    ShiftAssignmentFactory(organisation=org_a, shift=shift, membership=staff, date=_monday())
    body = login_as(manager_membership).get(f"/roster/?loc={location.id}").content.decode()
    assert "Morning" in body
    assert "Peter Okello" in body


def test_staff_cannot_see_the_roster_grid(org_a, roster, login_as):
    _, _, staff = roster
    assert login_as(staff).get("/roster/").status_code == 403


def test_a_manager_adds_and_removes_someone_from_a_cell(
    org_a, manager_membership, roster, login_as
):
    _, shift, staff = roster
    client = login_as(manager_membership)
    cell = {"shift": str(shift.id), "date": _monday().isoformat(), "membership": str(staff.id)}

    response = client.post("/roster/cell/", {**cell, "op": "add"}, **HTMX)
    assert response.status_code == 200
    assert ShiftAssignment.unscoped.filter(shift=shift, membership=staff).count() == 1

    client.post("/roster/cell/", {**cell, "op": "add"}, **HTMX)  # idempotent
    assert ShiftAssignment.unscoped.filter(shift=shift, membership=staff).count() == 1

    client.post("/roster/cell/", {**cell, "op": "remove"}, **HTMX)
    assert not ShiftAssignment.unscoped.filter(shift=shift, membership=staff).exists()


def test_a_supervisor_can_view_but_not_edit_the_roster(org_a, roster, login_as):
    location, shift, staff = roster
    supervisor = MembershipFactory(organisation=org_a, role="supervisor")
    client = login_as(supervisor)
    assert client.get(f"/roster/?loc={location.id}").status_code == 200
    response = client.post(
        "/roster/cell/",
        {
            "shift": str(shift.id),
            "date": _monday().isoformat(),
            "membership": str(staff.id),
            "op": "add",
        },
        **HTMX,
    )
    assert response.status_code == 403


def test_a_member_of_another_organisation_cannot_be_rostered(
    org_a, manager_membership, roster, login_as
):
    _, shift, _ = roster
    outsider = MembershipFactory(organisation=OrganisationFactory())
    response = login_as(manager_membership).post(
        "/roster/cell/",
        {
            "shift": str(shift.id),
            "date": _monday().isoformat(),
            "membership": str(outsider.id),
            "op": "add",
        },
        **HTMX,
    )
    assert response.status_code == 404
    assert not ShiftAssignment.unscoped.filter(membership=outsider).exists()


def test_copy_last_week_is_idempotent(org_a, manager_membership, roster, login_as):
    location, shift, staff = roster
    this_monday = _monday()
    last_monday = this_monday - timedelta(days=7)
    ShiftAssignmentFactory(organisation=org_a, shift=shift, membership=staff, date=last_monday)
    week = this_monday.strftime("%G-W%V")

    client = login_as(manager_membership)
    client.post("/roster/copy-week/", {"week": week, "loc": str(location.id)})
    client.post("/roster/copy-week/", {"week": week, "loc": str(location.id)})

    copies = ShiftAssignment.unscoped.filter(shift=shift, membership=staff, date=this_monday)
    assert copies.count() == 1


def test_overlapping_shifts_are_warned_about(org_a, manager_membership, roster, login_as):
    location, shift, staff = roster
    overlapping = ShiftFactory(
        organisation=org_a,
        location=location,
        name="Mid",
        start_time=time(10, 0),
        end_time=time(18, 0),
    )
    day = _monday()
    ShiftAssignmentFactory(organisation=org_a, shift=shift, membership=staff, date=day)
    ShiftAssignmentFactory(organisation=org_a, shift=overlapping, membership=staff, date=day)

    body = login_as(manager_membership).get(f"/roster/?loc={location.id}").content.decode()
    assert "overlapping" in body.lower()
