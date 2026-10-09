"""Manager dashboard (docs/01-requirements.md F3, docs/04-design.md §3 S7,
§5.3 buckets): counts, breakdowns, the overdue list with one-tap reassign,
and 7/30-day completion trends.

Due times are placed between local midnight and now (or now and the next
midnight), so these tests hold at any hour of the day.
"""

from datetime import datetime, time, timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from checklists.models import ChecklistRunStatus
from dashboard import queries
from organisations.models import AuditEvent
from organisations.tenancy import tenant_context
from tasks.models import Task, TaskStatus
from tests.factories import (
    ChecklistRunFactory,
    LocationFactory,
    MembershipFactory,
    MembershipLocationFactory,
    OrganisationFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
    TaskFactory,
)

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def _today_bounds():
    start = datetime.combine(timezone.localdate(), time.min, tzinfo=timezone.get_current_timezone())
    return start, start + timedelta(days=1)


@pytest.fixture
def w(org_a):
    """Today at two locations: every bucket once, plus things that must not
    count (cancelled, another day, another organisation)."""
    now = timezone.now()
    start, end = _today_bounds()
    earlier = start + (now - start) / 2  # today, already past
    later = now + (end - now) / 2  # today, still ahead

    main = LocationFactory(organisation=org_a, name="Main building")
    restaurant = LocationFactory(organisation=org_a, name="Restaurant")
    morning = ShiftFactory(organisation=org_a, location=main, name="Morning")
    manager = MembershipFactory(organisation=org_a, role="manager")
    peter = MembershipFactory(organisation=org_a, role="staff", user__name="Peter Okello")
    grace = MembershipFactory(organisation=org_a, role="staff", user__name="Grace Nakato")

    def task(location=main, **kw):
        kw.setdefault("assignee_location", False)
        kw.setdefault("assignee_membership", peter)
        return TaskFactory(organisation=org_a, location=location, created_by=manager, **kw)

    world = SimpleNamespace(
        org=org_a,
        main=main,
        restaurant=restaurant,
        morning=morning,
        manager=manager,
        peter=peter,
        grace=grace,
        now=now,
        earlier=earlier,
        later=later,
    )
    world.done_on_time = task(
        title="Done on time",
        due_at=later,
        status=TaskStatus.DONE,
        completed_by=peter,
        completed_at_trusted=earlier,
        due_at_when_completed=later,
    )
    world.done_late = task(
        title="Done late",
        due_at=earlier,
        status=TaskStatus.DONE,
        completed_by=grace,
        completed_at_trusted=now,
        due_at_when_completed=earlier,
    )
    world.flagged = task(
        title="Flagged one",
        due_at=later,
        status=TaskStatus.FLAGGED,
        flag_kind="problem",
        location=restaurant,
        assignee_membership=grace,
    )
    world.overdue = task(
        title="Overdue shift task",
        due_at=earlier,
        assignee_membership=None,
        assignee_shift=morning,
        shift_date=timezone.localdate(),
    )
    world.open = task(title="Still open", due_at=later)
    task(title="Cancelled one", due_at=earlier, status=TaskStatus.CANCELLED)
    task(title="Tomorrow's", due_at=end + timedelta(hours=2))
    TaskFactory(organisation=OrganisationFactory(), title="Other org", due_at=earlier)

    world.run_done = ChecklistRunFactory(
        organisation=org_a,
        location=main,
        shift=morning,
        name="Opening run",
        occurrence_start=earlier - timedelta(minutes=1),
        due_at=earlier,
        status=ChecklistRunStatus.DONE,
        completed_at_trusted=earlier,
    )
    world.run_overdue = ChecklistRunFactory(
        organisation=org_a,
        location=restaurant,
        name="Closing run",
        occurrence_start=earlier - timedelta(minutes=1),
        due_at=earlier,
    )
    return world


def _summary(membership, **kw):
    with tenant_context(membership.organisation):
        return queries.summary(membership, queries.period_for("today"), **kw)


def test_counts_are_exclusive_buckets_over_tasks_and_runs(w):
    counts = _summary(w.manager)
    assert counts == {"done": 3, "done_late": 1, "overdue": 2, "flagged": 1, "open": 1}


def test_counts_can_be_filtered_to_one_location(w):
    counts = _summary(w.manager, location_id=w.restaurant.id)
    assert counts == {"done": 0, "done_late": 0, "overdue": 1, "flagged": 1, "open": 0}


def test_a_supervisor_sees_only_their_locations(w):
    supervisor = MembershipFactory(organisation=w.org, role="supervisor")
    MembershipLocationFactory(organisation=w.org, membership=supervisor, location=w.restaurant)
    assert _summary(supervisor) == {
        "done": 0,
        "done_late": 0,
        "overdue": 1,
        "flagged": 1,
        "open": 0,
    }


def _breakdown(w, group):
    with tenant_context(w.org):
        rows = queries.breakdown(w.manager, queries.period_for("today"), group)
    return {
        row["label"]: {k: row[k] for k in ("done", "overdue", "flagged", "open")} for row in rows
    }


def test_breakdown_by_location(w):
    rows = _breakdown(w, "location")
    assert rows["Main building"] == {"done": 3, "overdue": 1, "flagged": 0, "open": 1}
    assert rows["Restaurant"] == {"done": 0, "overdue": 1, "flagged": 1, "open": 0}


def test_breakdown_by_shift_puts_unshifted_work_in_its_own_row(w):
    rows = _breakdown(w, "shift")
    assert rows["Morning"] == {"done": 1, "overdue": 1, "flagged": 0, "open": 0}
    assert rows["Not on a shift"] == {"done": 2, "overdue": 1, "flagged": 1, "open": 1}


def test_breakdown_by_staff_credits_whoever_completed_the_work(w):
    rows = _breakdown(w, "staff")
    assert rows["Peter Okello"] == {"done": 1, "overdue": 0, "flagged": 0, "open": 1}
    assert rows["Grace Nakato"] == {"done": 1, "overdue": 0, "flagged": 1, "open": 0}
    assert rows["Shift or location tasks (unclaimed)"]["overdue"] == 1
    assert rows["Checklists"] == {"done": 1, "overdue": 1, "flagged": 0, "open": 0}


def test_the_overdue_list_has_tasks_and_runs(w):
    with tenant_context(w.org):
        items = queries.overdue_items(w.manager, queries.period_for("today"))
    assert [t.title for t in items["tasks"]] == ["Overdue shift task"]
    assert [r.name for r in items["runs"]] == ["Closing run"]


# --- trends ---


def test_trend_has_one_point_per_full_day_with_gaps_for_days_without_work(w):
    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    two_days_ago = datetime.combine(today - timedelta(days=2), time(10), tzinfo=tz)
    for status in (TaskStatus.DONE, TaskStatus.DONE, TaskStatus.PENDING, TaskStatus.CANCELLED):
        TaskFactory(
            organisation=w.org,
            location=w.main,
            created_by=w.manager,
            due_at=two_days_ago,
            status=status,
            completed_at_trusted=two_days_ago if status == TaskStatus.DONE else None,
            due_at_when_completed=two_days_ago if status == TaskStatus.DONE else None,
        )
    with tenant_context(w.org):
        trend = queries.trend(w.manager, days=30)

    assert len(trend["series"]) == 30
    assert trend["series"][-1]["day"] == today - timedelta(days=1)  # today isn't over yet
    point = next(p for p in trend["series"] if p["day"] == today - timedelta(days=2))
    assert (point["done"], point["total"], point["rate"]) == (2, 3, 67)  # cancelled excluded
    assert next(p for p in trend["series"] if p["day"] == today - timedelta(days=5))["rate"] is None
    assert trend["rate_7"] == 67
    assert trend["rate_30"] == 67


# --- views ---


def test_staff_cannot_open_the_dashboard(w, login_as):
    assert login_as(w.peter).get("/dashboard/").status_code == 403


def test_the_dashboard_shows_counts_breakdown_overdue_and_trend(w, login_as):
    body = login_as(w.manager).get("/dashboard/").content.decode()
    assert "Overdue shift task" in body
    assert "Closing run" in body
    assert "Main building" in body
    assert "Completion" in body
    assert "<svg" in body


def test_the_htmx_summary_is_a_partial(w, login_as):
    response = login_as(w.manager).get("/dashboard/partials/summary?group=shift", **HTMX)
    assert response.status_code == 200
    assert b"<html" not in response.content
    assert b"Not on a shift" in response.content


def test_the_drill_down_lists_one_bucket(w, login_as):
    body = login_as(w.manager).get("/dashboard/list?bucket=flagged&range=today").content.decode()
    assert "Flagged one" in body
    assert "Still open" not in body


def test_the_dashboard_query_count_does_not_grow_with_the_data(
    w, login_as, django_assert_max_num_queries
):
    client = login_as(w.manager)
    client.get("/dashboard/")  # warm up (session, etc.)
    for i in range(30):
        TaskFactory(
            organisation=w.org,
            location=w.main,
            created_by=w.manager,
            title=f"More {i}",
            due_at=w.earlier,
            assignee_location=False,
            assignee_membership=w.peter,
        )
    with django_assert_max_num_queries(25):
        assert client.get("/dashboard/").status_code == 200


# --- one-tap reassign ---


def test_reassigning_an_overdue_task_to_a_person(w, login_as):
    response = login_as(w.manager).post(
        f"/dashboard/reassign/{w.overdue.id}/", {"membership": str(w.grace.id)}, **HTMX
    )
    assert response.status_code == 200
    assert b"Grace Nakato" in response.content
    w.overdue.refresh_from_db()
    assert w.overdue.assignee_membership == w.grace
    assert w.overdue.assignee_shift is None
    assert w.overdue.shift_date is None
    assert AuditEvent.unscoped.filter(action="task.edit", target_id=w.overdue.id).exists()


def test_people_on_shift_today_are_offered_first(w, login_as):
    ShiftAssignmentFactory(
        organisation=w.org, shift=w.morning, membership=w.grace, date=timezone.localdate()
    )
    body = login_as(w.manager).get("/dashboard/").content.decode()
    picker = body[body.index("Overdue shift task") :]
    assert picker.index("Grace Nakato") < picker.index("Peter Okello")


def test_staff_cannot_reassign(w, login_as):
    response = login_as(w.peter).post(
        f"/dashboard/reassign/{w.overdue.id}/", {"membership": str(w.grace.id)}, **HTMX
    )
    assert response.status_code == 403


def test_reassigning_to_someone_in_another_organisation_is_a_404(w, login_as):
    outsider = MembershipFactory(organisation=OrganisationFactory())
    response = login_as(w.manager).post(
        f"/dashboard/reassign/{w.overdue.id}/", {"membership": str(outsider.id)}, **HTMX
    )
    assert response.status_code == 404
    w.overdue.refresh_from_db()
    assert w.overdue.assignee_shift == w.morning


def test_a_finished_task_cannot_be_reassigned(w, login_as):
    response = login_as(w.manager).post(
        f"/dashboard/reassign/{w.done_late.id}/", {"membership": str(w.peter.id)}, **HTMX
    )
    assert response.status_code == 409
    assert Task.unscoped.get(pk=w.done_late.pk).completed_by == w.grace
