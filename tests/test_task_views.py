"""Task HTMX screens (docs/04-design.md §3 S4/S5/S8, §4.1): create/edit/
reassign, detail + actions, comments, "My tasks today", the filtered list."""

from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from organisations.models import AuditEvent
from tasks.models import Task, TaskComment, TaskStatus
from tests.factories import (
    LocationFactory,
    MembershipFactory,
    OrganisationFactory,
    ShiftFactory,
    TaskCommentFactory,
    TaskFactory,
)

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def w(org_a):
    location = LocationFactory(organisation=org_a, name="Main building")
    world = SimpleNamespace(
        org=org_a,
        location=location,
        manager=MembershipFactory(organisation=org_a, role="manager"),
        supervisor=MembershipFactory(organisation=org_a, role="supervisor"),
        staff=MembershipFactory(organisation=org_a, role="staff", user__name="Peter Okello"),
        other_staff=MembershipFactory(organisation=org_a, role="staff"),
    )
    world.task = TaskFactory(
        organisation=org_a,
        location=location,
        assignee_location=False,
        assignee_membership=world.staff,
        created_by=world.manager,
        photo_required=False,
        title="Clean Room 12",
    )
    return world


def _form(w, **overrides):
    due = timezone.localdate() + timedelta(days=2)
    data = {
        "title": "Restock towels",
        "description": "",
        "location": str(w.location.id),
        "assign_type": "person",
        "assignee_membership": str(w.staff.id),
        "due_date": due.isoformat(),
        "due_time": "09:30",
        "photo_required": "on",
        "reminder_lead_min": "",
    }
    data.update(overrides)
    return data


# --- create ---


def test_staff_cannot_open_the_create_form(w, login_as):
    assert login_as(w.staff).get("/tasks/new/").status_code == 403


def test_supervisor_creates_a_person_task(w, login_as):
    response = login_as(w.supervisor).post("/tasks/new/", _form(w))
    task = Task.unscoped.get(title="Restock towels")
    assert response.status_code == 302
    assert response.url == f"/tasks/{task.id}/"
    assert task.assignee_membership == w.staff
    assert task.created_by == w.supervisor
    assert task.status == TaskStatus.PENDING
    assert AuditEvent.unscoped.filter(action="task.create", target_id=task.id).exists()


def test_due_time_is_entered_in_the_organisations_timezone(w, login_as):
    """Africa/Kampala is UTC+3: 09:30 local is 06:30 UTC."""
    login_as(w.supervisor).post("/tasks/new/", _form(w))
    task = Task.unscoped.get(title="Restock towels")
    expected_day = timezone.localdate() + timedelta(days=2)
    assert task.due_at == datetime.combine(expected_day, time(6, 30), tzinfo=UTC)


def test_a_shift_task_needs_a_shift_at_the_same_location(w, login_as):
    other_location = LocationFactory(organisation=w.org)
    wrong_shift = ShiftFactory(organisation=w.org, location=other_location)
    response = login_as(w.supervisor).post(
        "/tasks/new/",
        _form(
            w,
            assign_type="shift",
            assignee_shift=str(wrong_shift.id),
            shift_date=timezone.localdate().isoformat(),
        ),
    )
    assert response.status_code == 200
    assert not Task.unscoped.filter(title="Restock towels").exists()


def test_a_shift_task_is_created_with_its_date(w, login_as):
    shift = ShiftFactory(organisation=w.org, location=w.location)
    day = timezone.localdate() + timedelta(days=1)
    login_as(w.supervisor).post(
        "/tasks/new/",
        _form(w, assign_type="shift", assignee_shift=str(shift.id), shift_date=day.isoformat()),
    )
    task = Task.unscoped.get(title="Restock towels")
    assert task.assignee_shift == shift
    assert task.shift_date == day
    assert task.assignee_membership is None


def test_a_location_from_another_organisation_is_not_a_valid_choice(w, login_as):
    foreign = LocationFactory(organisation=OrganisationFactory())
    response = login_as(w.supervisor).post("/tasks/new/", _form(w, location=str(foreign.id)))
    assert response.status_code == 200
    assert not Task.unscoped.filter(title="Restock towels").exists()


def test_a_due_time_well_in_the_past_is_rejected(w, login_as):
    yesterday = timezone.localdate() - timedelta(days=1)
    response = login_as(w.supervisor).post("/tasks/new/", _form(w, due_date=yesterday.isoformat()))
    assert response.status_code == 200
    assert not Task.unscoped.filter(title="Restock towels").exists()


# --- edit / reassign ---


def test_reassign_from_a_person_to_the_whole_location(w, login_as):
    response = login_as(w.supervisor).post(
        f"/tasks/{w.task.id}/edit/", _form(w, title="Clean Room 12", assign_type="location")
    )
    assert response.status_code == 302
    w.task.refresh_from_db()
    assert w.task.assignee_location is True
    assert w.task.assignee_membership is None
    event = AuditEvent.unscoped.get(action="task.edit", target_id=w.task.id)
    assert "assignee_membership" in event.changes


def test_a_finished_task_cannot_be_edited(w, login_as):
    Task.unscoped.filter(pk=w.task.pk).update(status=TaskStatus.DONE)
    response = login_as(w.supervisor).post(f"/tasks/{w.task.id}/edit/", _form(w))
    assert response.status_code == 409


# --- detail + visibility ---


def test_the_assignee_can_open_their_task(w, login_as):
    response = login_as(w.staff).get(f"/tasks/{w.task.id}/")
    assert response.status_code == 200
    assert b"Clean Room 12" in response.content


def test_other_staff_in_the_same_org_get_403(w, login_as):
    assert login_as(w.other_staff).get(f"/tasks/{w.task.id}/").status_code == 403


def test_another_organisation_gets_404(w, login_as):
    outsider = MembershipFactory(organisation=OrganisationFactory(), role="owner")
    assert login_as(outsider).get(f"/tasks/{w.task.id}/").status_code == 404


# --- actions ---


def test_starting_over_htmx_returns_the_updated_panel(w, login_as):
    response = login_as(w.staff).post(f"/tasks/{w.task.id}/start/", **HTMX)
    assert response.status_code == 200
    assert b"In progress" in response.content
    w.task.refresh_from_db()
    assert w.task.status == TaskStatus.IN_PROGRESS


def test_starting_without_htmx_redirects_back_to_the_task(w, login_as):
    response = login_as(w.staff).post(f"/tasks/{w.task.id}/start/")
    assert response.status_code == 302
    assert response.url == f"/tasks/{w.task.id}/"


def test_completing_without_a_required_photo_explains_why(w, login_as):
    Task.unscoped.filter(pk=w.task.pk).update(photo_required=True)
    response = login_as(w.staff).post(f"/tasks/{w.task.id}/complete/", **HTMX)
    assert response.status_code == 200
    assert b"Add a photo" in response.content
    w.task.refresh_from_db()
    assert w.task.status == TaskStatus.PENDING


def test_flagging_over_htmx(w, login_as):
    response = login_as(w.staff).post(
        f"/tasks/{w.task.id}/flag/", {"reason": "No supplies", "note": ""}, **HTMX
    )
    assert response.status_code == 200
    w.task.refresh_from_db()
    assert w.task.status == TaskStatus.FLAGGED


def test_an_action_the_user_may_not_take_is_403(w, login_as):
    assert login_as(w.staff).post(f"/tasks/{w.task.id}/cancel/", **HTMX).status_code == 403


# --- comments ---


def test_adding_a_comment(w, login_as):
    response = login_as(w.staff).post(
        f"/tasks/{w.task.id}/comments/", {"body": "On it now"}, **HTMX
    )
    assert response.status_code == 200
    assert b"On it now" in response.content
    assert TaskComment.unscoped.filter(task=w.task, author=w.staff).exists()


def test_an_overlong_comment_is_rejected(w, login_as):
    login_as(w.staff).post(f"/tasks/{w.task.id}/comments/", {"body": "x" * 501}, **HTMX)
    assert not TaskComment.unscoped.filter(task=w.task).exists()


def test_the_author_can_delete_a_comment_within_five_minutes(w, login_as):
    comment = TaskCommentFactory(organisation=w.org, task=w.task, author=w.staff)
    login_as(w.staff).post(f"/tasks/{w.task.id}/comments/{comment.id}/delete/", **HTMX)
    comment.refresh_from_db()
    assert comment.deleted_at is not None


def test_a_comment_cannot_be_deleted_after_five_minutes(w, login_as):
    comment = TaskCommentFactory(organisation=w.org, task=w.task, author=w.staff)
    TaskComment.unscoped.filter(pk=comment.pk).update(
        created_at=timezone.now() - timedelta(minutes=6)
    )
    response = login_as(w.staff).post(f"/tasks/{w.task.id}/comments/{comment.id}/delete/", **HTMX)
    assert response.status_code == 403
    comment.refresh_from_db()
    assert comment.deleted_at is None


def test_nobody_can_delete_someone_elses_comment(w, login_as):
    comment = TaskCommentFactory(organisation=w.org, task=w.task, author=w.staff)
    response = login_as(w.supervisor).post(
        f"/tasks/{w.task.id}/comments/{comment.id}/delete/", **HTMX
    )
    assert response.status_code == 403


# --- lists ---


def test_my_tasks_shows_my_work_with_overdue_first(w, login_as):
    TaskFactory(
        organisation=w.org,
        location=w.location,
        assignee_location=False,
        assignee_membership=w.staff,
        created_by=w.manager,
        title="Late one",
        due_at=timezone.now() - timedelta(hours=1),
    )
    TaskFactory(
        organisation=w.org,
        location=w.location,
        assignee_location=False,
        assignee_membership=w.other_staff,
        created_by=w.manager,
        title="Not mine",
    )
    body = login_as(w.staff).get("/tasks/my/").content.decode()
    assert "Late one" in body
    assert "Clean Room 12" in body
    assert "Not mine" not in body
    assert body.index("Late one") < body.index("Clean Room 12")


def test_staff_land_on_the_offline_field_app(w, login_as):
    response = login_as(w.staff).get("/")
    assert response.status_code == 302
    assert response.url == "/app/"


def test_the_task_list_filters_to_overdue(w, login_as):
    TaskFactory(
        organisation=w.org,
        location=w.location,
        created_by=w.manager,
        title="Late one",
        due_at=timezone.now() - timedelta(hours=1),
    )
    body = login_as(w.supervisor).get("/tasks/?bucket=overdue").content.decode()
    assert "Late one" in body
    assert "Clean Room 12" not in body


def test_the_task_list_is_supervisor_only(w, login_as):
    assert login_as(w.staff).get("/tasks/").status_code == 403


def test_the_assignee_picker_lists_only_shifts_at_the_chosen_location(w, login_as):
    here = ShiftFactory(organisation=w.org, location=w.location, name="Morning")
    ShiftFactory(organisation=w.org, location=LocationFactory(organisation=w.org), name="Night")
    body = (
        login_as(w.supervisor)
        .get(f"/tasks/partials/assignees?location={w.location.id}", **HTMX)
        .content.decode()
    )
    assert str(here.id) in body
    assert "Night" not in body
