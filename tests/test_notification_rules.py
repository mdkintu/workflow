"""notifications.rules: quiet hours, retry backoff, who's on shift, and who
gets alerted (docs/01-requirements.md F5, docs/02-architecture.md §5-6)."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from notifications import rules
from organisations.tenancy import tenant_context
from tests.factories import (
    LocationFactory,
    MembershipFactory,
    MembershipLocationFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
)

pytestmark = pytest.mark.django_db

KLA = ZoneInfo("Africa/Kampala")


def at(hour, minute=0, day=14):
    return datetime(2031, 10, day, hour, minute, tzinfo=KLA)


@pytest.mark.parametrize(
    ("when", "quiet"),
    [
        (at(23), True),
        (at(2), True),
        (at(5, 59), True),
        (at(6), False),
        (at(12), False),
        (at(21, 59), False),
        (at(22), True),
    ],
)
def test_default_quiet_hours_wrap_past_midnight(org_a, when, quiet):
    assert rules.in_quiet_hours(org_a, when) is quiet


def test_quiet_hours_that_dont_cross_midnight(org_a):
    org_a.quiet_hours_start, org_a.quiet_hours_end = time(13, 0), time(14, 0)
    assert rules.in_quiet_hours(org_a, at(13, 30)) is True
    assert rules.in_quiet_hours(org_a, at(14, 0)) is False


def test_quiet_hours_end_is_the_next_morning(org_a):
    assert rules.quiet_end(org_a, at(23)) == at(6, day=15)
    assert rules.quiet_end(org_a, at(2)) == at(6)


@pytest.mark.parametrize(
    ("attempts", "minutes"), [(1, 1), (2, 2), (3, 4), (4, 8), (7, 60), (20, 60)]
)
def test_backoff_doubles_up_to_an_hour(attempts, minutes):
    assert rules.backoff(attempts) == timedelta(minutes=minutes)


@pytest.fixture
def main(org_a):
    return LocationFactory(organisation=org_a)


def test_on_shift_includes_an_overnight_shift_that_started_yesterday(org_a, main):
    night = ShiftFactory(
        organisation=org_a, location=main, name="Night", start_time=time(22, 0), end_time=time(6, 0)
    )
    guard = MembershipFactory(organisation=org_a, role="staff")
    ShiftAssignmentFactory(
        organisation=org_a, shift=night, membership=guard, date=at(0).date() - timedelta(days=1)
    )
    with tenant_context(org_a):
        assert rules.is_on_shift(guard, at(3)) is True
        assert rules.is_on_shift(guard, at(7)) is False


def test_the_supervisor_on_duty_is_the_one_rostered_there_now(org_a, main):
    morning = ShiftFactory(organisation=org_a, location=main, start_time=time(6), end_time=time(14))
    on_duty = MembershipFactory(organisation=org_a, role="supervisor")
    MembershipFactory(organisation=org_a, role="supervisor")  # not rostered
    ShiftAssignmentFactory(organisation=org_a, shift=morning, membership=on_duty, date=at(0).date())
    with tenant_context(org_a):
        assert rules.supervisors_on_duty(main, at(10)) == [on_duty]


def test_without_a_rostered_supervisor_those_linked_to_the_location_are_used(org_a, main):
    linked = MembershipFactory(organisation=org_a, role="supervisor")
    MembershipLocationFactory(organisation=org_a, membership=linked, location=main)
    MembershipFactory(organisation=org_a, role="supervisor")
    with tenant_context(org_a):
        assert rules.supervisors_on_duty(main, at(10)) == [linked]


def test_with_no_supervisors_at_all_managers_are_used(org_a, main):
    manager = MembershipFactory(organisation=org_a, role="manager")
    with tenant_context(org_a):
        assert rules.supervisors_on_duty(main, at(10)) == [manager]


def test_managers_for_a_location_are_those_linked_or_unrestricted(org_a, main):
    elsewhere = LocationFactory(organisation=org_a)
    here = MembershipFactory(organisation=org_a, role="manager")
    MembershipLocationFactory(organisation=org_a, membership=here, location=main)
    other = MembershipFactory(organisation=org_a, role="manager")
    MembershipLocationFactory(organisation=org_a, membership=other, location=elsewhere)
    everywhere = MembershipFactory(organisation=org_a, role="manager")
    with tenant_context(org_a):
        assert set(rules.managers_for(main)) == {here, everywhere}
