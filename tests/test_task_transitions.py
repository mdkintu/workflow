"""tasks.transitions: the task state machine (docs/04-design.md §5.1-5.2,
ADR-17). The only code allowed to change Task.status (CLAUDE.md)."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from organisations.models import AuditEvent
from tasks.models import FlagKind, Task, TaskComment, TaskStatus
from tasks.transitions import Action, allowed_actions, apply
from tests.factories import (
    LocationFactory,
    MembershipFactory,
    MembershipLocationFactory,
    OrganisationFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
    TaskFactory,
    TaskPhotoFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def w(org_a):
    """A small world: one location, one of each role, a task for `staff`."""
    location = LocationFactory(organisation=org_a)
    manager = MembershipFactory(organisation=org_a, role="manager")
    world = SimpleNamespace(
        org=org_a,
        location=location,
        manager=manager,
        supervisor=MembershipFactory(organisation=org_a, role="supervisor"),
        staff=MembershipFactory(organisation=org_a, role="staff"),
        other_staff=MembershipFactory(organisation=org_a, role="staff"),
    )
    world.task = TaskFactory(
        organisation=org_a,
        location=location,
        assignee_location=False,
        assignee_membership=world.staff,
        created_by=manager,
        photo_required=False,
    )
    return world


def _status(task):
    task.refresh_from_db()
    return task.status


# --- start ---


def test_assignee_can_start_a_pending_task(w):
    result = apply(w.task, Action.START, w.staff)
    assert result.ok
    assert _status(w.task) == TaskStatus.IN_PROGRESS
    assert w.task.started_by == w.staff
    assert AuditEvent.unscoped.filter(action="task.start", target_id=w.task.id).exists()


def test_another_staff_member_cannot_start_someone_elses_task(w):
    result = apply(w.task, Action.START, w.other_staff)
    assert not result.ok
    assert result.code == "forbidden"
    assert _status(w.task) == TaskStatus.PENDING


def test_start_on_a_done_task_is_an_invalid_transition(w):
    Task.unscoped.filter(pk=w.task.pk).update(status=TaskStatus.DONE)
    w.task.refresh_from_db()
    result = apply(w.task, Action.START, w.staff)
    assert result.code == "invalid_transition"


def test_an_actor_from_another_organisation_gets_not_found(w):
    outsider = MembershipFactory(organisation=OrganisationFactory(), role="owner")
    result = apply(w.task, Action.CANCEL, outsider)
    assert result.code == "not_found"
    assert _status(w.task) == TaskStatus.PENDING


# --- complete ---


def test_complete_straight_from_pending_records_who_and_when(w):
    result = apply(w.task, Action.COMPLETE, w.staff)
    assert result.ok
    w.task.refresh_from_db()
    assert w.task.status == TaskStatus.DONE
    assert w.task.completed_by == w.staff
    assert w.task.completed_received_at is not None
    assert w.task.completed_at_trusted is not None
    assert w.task.due_at_when_completed == w.task.due_at


def test_complete_is_blocked_without_a_photo_when_one_is_required(w):
    Task.unscoped.filter(pk=w.task.pk).update(photo_required=True)
    w.task.refresh_from_db()
    result = apply(w.task, Action.COMPLETE, w.staff)
    assert result.code == "photo_required"
    assert _status(w.task) == TaskStatus.PENDING


def test_complete_succeeds_once_a_photo_is_attached(w):
    Task.unscoped.filter(pk=w.task.pk).update(photo_required=True)
    w.task.refresh_from_db()
    TaskPhotoFactory(organisation=w.org, task=w.task, uploaded_by=w.staff, linked_at=timezone.now())
    assert apply(w.task, Action.COMPLETE, w.staff).ok
    assert _status(w.task) == TaskStatus.DONE


# --- flag / complete anyway ---


def test_flag_needs_a_reason(w):
    result = apply(w.task, Action.FLAG, w.staff, reason="")
    assert result.code == "validation_error"
    assert _status(w.task) == TaskStatus.PENDING


def test_flag_marks_a_problem_and_adds_a_flag_comment(w):
    result = apply(w.task, Action.FLAG, w.staff, reason="No supplies", note="Store locked")
    assert result.ok
    w.task.refresh_from_db()
    assert w.task.status == TaskStatus.FLAGGED
    assert w.task.flag_kind == FlagKind.PROBLEM
    assert w.task.flagged_by == w.staff
    comment = TaskComment.unscoped.get(task=w.task)
    assert comment.is_flag
    assert "No supplies" in comment.body


def test_staff_can_complete_a_task_they_flagged_as_a_problem(w):
    apply(w.task, Action.FLAG, w.staff, reason="No supplies")
    w.task.refresh_from_db()
    assert apply(w.task, Action.COMPLETE, w.staff).ok
    w.task.refresh_from_db()
    assert w.task.status == TaskStatus.DONE
    assert w.task.flag_kind is None
    assert w.task.flag_resolved_note


def test_a_rejected_task_cannot_just_be_completed_again(w):
    apply(w.task, Action.COMPLETE, w.staff)
    w.task.refresh_from_db()
    apply(w.task, Action.REJECT, w.supervisor, reason="Bed not made")
    w.task.refresh_from_db()
    assert apply(w.task, Action.COMPLETE, w.staff).code == "invalid_transition"


# --- reject / resolve ---


def test_staff_cannot_reject(w):
    apply(w.task, Action.COMPLETE, w.staff)
    w.task.refresh_from_db()
    assert apply(w.task, Action.REJECT, w.other_staff, reason="x").code == "forbidden"


def test_supervisor_reject_needs_a_reason(w):
    apply(w.task, Action.COMPLETE, w.staff)
    w.task.refresh_from_db()
    assert apply(w.task, Action.REJECT, w.supervisor, reason=" ").code == "validation_error"


def test_supervisor_rejects_a_done_task_keeping_the_completion(w):
    apply(w.task, Action.COMPLETE, w.staff)
    w.task.refresh_from_db()
    assert apply(w.task, Action.REJECT, w.supervisor, reason="Bed not made").ok
    w.task.refresh_from_db()
    assert w.task.status == TaskStatus.FLAGGED
    assert w.task.flag_kind == FlagKind.REJECTED
    assert w.task.completed_by == w.staff
    assert TaskComment.unscoped.filter(task=w.task, body__contains="Bed not made").exists()


def test_resolving_a_flag_sends_the_task_back_to_in_progress(w):
    apply(w.task, Action.FLAG, w.staff, reason="No supplies")
    w.task.refresh_from_db()
    assert apply(w.task, Action.RESOLVE_FLAG, w.supervisor, note="Delivered").ok
    w.task.refresh_from_db()
    assert w.task.status == TaskStatus.IN_PROGRESS
    assert w.task.flag_kind is None
    assert w.task.flag_resolved_note == "Delivered"


# --- cancel ---


def test_supervisor_can_cancel_an_open_task(w):
    assert apply(w.task, Action.CANCEL, w.supervisor).ok
    w.task.refresh_from_db()
    assert w.task.status == TaskStatus.CANCELLED
    assert w.task.cancelled_by == w.supervisor


def test_staff_cannot_cancel(w):
    assert apply(w.task, Action.CANCEL, w.staff).code == "forbidden"


def test_a_done_task_cannot_be_cancelled(w):
    apply(w.task, Action.COMPLETE, w.staff)
    w.task.refresh_from_db()
    assert apply(w.task, Action.CANCEL, w.manager).code == "invalid_transition"


# --- scope ---


def test_supervisor_limited_to_other_locations_cannot_act(w):
    other_location = LocationFactory(organisation=w.org)
    MembershipLocationFactory(organisation=w.org, membership=w.supervisor, location=other_location)
    assert apply(w.task, Action.CANCEL, w.supervisor).code == "forbidden"


def test_rostered_staff_can_act_on_a_shift_task(w):
    shift = ShiftFactory(organisation=w.org, location=w.location)
    today = timezone.localdate()
    task = TaskFactory(
        organisation=w.org,
        location=w.location,
        assignee_location=False,
        assignee_shift=shift,
        shift_date=today,
        created_by=w.manager,
        photo_required=False,
    )
    ShiftAssignmentFactory(organisation=w.org, shift=shift, membership=w.other_staff, date=today)
    assert apply(task, Action.START, w.other_staff).ok
    assert apply(task, Action.COMPLETE, w.staff).code == "forbidden"  # not rostered


def test_allowed_actions_drive_the_buttons(w):
    assert allowed_actions(w.task, w.staff) == [Action.START, Action.COMPLETE, Action.FLAG]
    assert Action.CANCEL in allowed_actions(w.task, w.supervisor)
    assert allowed_actions(w.task, w.other_staff) == []


def test_an_overdue_task_can_still_be_completed(w):
    Task.unscoped.filter(pk=w.task.pk).update(due_at=timezone.now() - timedelta(hours=3))
    w.task.refresh_from_db()
    assert apply(w.task, Action.COMPLETE, w.staff).ok
