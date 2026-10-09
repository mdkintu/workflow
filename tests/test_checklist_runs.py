"""checklists.runs: ticking/skipping items and the run state machine
(docs/04-design.md §5.4, docs/01-requirements.md F2.3), plus
ChecklistRun.objects.visible_to()."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from checklists import runs
from checklists.models import ChecklistRun, ChecklistRunItemTick, ChecklistRunStatus
from organisations.models import AuditEvent
from organisations.tenancy import tenant_context
from tasks.models import TaskComment
from tests.factories import (
    ChecklistRunFactory,
    ChecklistRunItemFactory,
    LocationFactory,
    MembershipFactory,
    MembershipLocationFactory,
    OrganisationFactory,
    RecurrenceRuleFactory,
    ShiftAssignmentFactory,
    ShiftFactory,
    TaskPhotoFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def w(org_a):
    location = LocationFactory(organisation=org_a)
    shift = ShiftFactory(organisation=org_a, location=location)
    today = timezone.localdate()
    world = SimpleNamespace(
        org=org_a,
        location=location,
        shift=shift,
        staff=MembershipFactory(organisation=org_a, role="staff"),
        other_staff=MembershipFactory(organisation=org_a, role="staff"),
        supervisor=MembershipFactory(organisation=org_a, role="supervisor"),
    )
    ShiftAssignmentFactory(organisation=org_a, shift=shift, membership=world.staff, date=today)
    world.run = ChecklistRunFactory(
        organisation=org_a,
        location=location,
        shift=shift,
        shift_date=today,
        occurrence_start=timezone.now() - timedelta(minutes=10),
        due_at=timezone.now() + timedelta(hours=1),
    )
    world.plain = ChecklistRunItemFactory(organisation=org_a, run=world.run, order=1)
    world.photo_item = ChecklistRunItemFactory(
        organisation=org_a, run=world.run, order=2, photo_required=True
    )
    world.must_do = ChecklistRunItemFactory(
        organisation=org_a, run=world.run, order=3, skippable=False
    )
    return world


def _status(run):
    run.refresh_from_db()
    return run.status


def _photo(w):
    return TaskPhotoFactory(
        organisation=w.org,
        task=None,
        checklist_run=w.run,
        uploaded_by=w.staff,
        linked_at=timezone.now(),
    )


def test_the_first_tick_starts_the_run(w):
    result = runs.tick_item(w.plain, w.staff)
    assert result.ok
    assert _status(w.run) == ChecklistRunStatus.IN_PROGRESS
    tick = ChecklistRunItemTick.unscoped.get(run_item=w.plain)
    assert tick.membership == w.staff
    assert not tick.skipped


def test_ticking_every_item_finishes_the_run(w):
    runs.tick_item(w.plain, w.staff)
    runs.tick_item(w.photo_item, w.staff, photo=_photo(w))
    runs.tick_item(w.must_do, w.staff)
    w.run.refresh_from_db()
    assert w.run.status == ChecklistRunStatus.DONE
    assert w.run.completed_at_trusted is not None


def test_a_photo_item_needs_a_photo(w):
    assert runs.tick_item(w.photo_item, w.staff).code == "photo_required"
    assert not ChecklistRunItemTick.unscoped.filter(run_item=w.photo_item).exists()


def test_the_photo_is_linked_to_the_tick(w):
    photo = _photo(w)
    runs.tick_item(w.photo_item, w.staff, photo=photo)
    assert ChecklistRunItemTick.unscoped.get(run_item=w.photo_item).photo == photo


def test_ticking_an_item_twice_keeps_the_first_tick(w):
    runs.tick_item(w.plain, w.staff)
    result = runs.tick_item(w.plain, w.supervisor)
    assert result.ok and result.code == "already_done"
    assert ChecklistRunItemTick.unscoped.filter(run_item=w.plain).count() == 1


def test_a_skip_needs_a_reason(w):
    assert runs.skip_item(w.plain, w.staff, reason=" ").code == "validation_error"


def test_a_must_do_item_cannot_be_skipped(w):
    assert runs.skip_item(w.must_do, w.staff, reason="No time").code == "not_skippable"


def test_finishing_with_a_skip_flags_the_run(w):
    runs.skip_item(w.plain, w.staff, reason="Machine broken")
    runs.tick_item(w.photo_item, w.staff, photo=_photo(w))
    runs.tick_item(w.must_do, w.staff)
    assert _status(w.run) == ChecklistRunStatus.FLAGGED
    tick = ChecklistRunItemTick.unscoped.get(run_item=w.plain)
    assert tick.skipped and tick.skip_reason == "Machine broken"


def test_staff_not_on_the_shift_cannot_tick(w):
    assert runs.tick_item(w.plain, w.other_staff).code == "forbidden"


def test_a_location_run_is_open_to_anyone_rostered_at_that_location(w):
    location_run = ChecklistRunFactory(
        organisation=w.org,
        location=w.location,
        shift=None,
        shift_date=None,
        occurrence_start=timezone.now(),
        due_at=timezone.now() + timedelta(hours=1),
    )
    item = ChecklistRunItemFactory(organisation=w.org, run=location_run, order=1)
    assert runs.tick_item(item, w.staff).ok
    assert (
        runs.tick_item(
            ChecklistRunItemFactory(organisation=w.org, run=location_run, order=2), w.other_staff
        ).code
        == "forbidden"
    )


def test_a_run_that_isnt_available_yet_cannot_be_ticked(w):
    rule = RecurrenceRuleFactory(organisation=w.org, available_before_min=15)
    ChecklistRun.unscoped.filter(pk=w.run.pk).update(
        rule=rule, occurrence_start=timezone.now() + timedelta(hours=2)
    )
    w.plain.refresh_from_db()
    assert runs.tick_item(w.plain, w.staff).code == "not_yet_available"


def test_a_finished_or_cancelled_run_cannot_be_ticked(w):
    ChecklistRun.unscoped.filter(pk=w.run.pk).update(status=ChecklistRunStatus.CANCELLED)
    assert runs.tick_item(w.plain, w.staff).code == "invalid_transition"


def test_reporting_a_problem_flags_the_run_with_a_flag_comment(w):
    result = runs.flag_run(w.run, w.staff, reason="No supplies", note="Stores locked")
    assert result.ok
    assert _status(w.run) == ChecklistRunStatus.FLAGGED
    comment = TaskComment.unscoped.get(checklist_run=w.run)
    assert comment.is_flag and "No supplies" in comment.body


def test_items_can_still_be_ticked_on_a_flagged_run(w):
    runs.flag_run(w.run, w.staff, reason="No supplies")
    assert runs.tick_item(w.plain, w.staff).ok
    assert _status(w.run) == ChecklistRunStatus.FLAGGED  # until a supervisor resolves it


def test_resolving_a_flag_finishes_the_run_if_everything_is_resolved(w):
    runs.skip_item(w.plain, w.staff, reason="Machine broken")
    runs.tick_item(w.photo_item, w.staff, photo=_photo(w))
    runs.tick_item(w.must_do, w.staff)
    assert runs.resolve_run_flag(w.run, w.supervisor, note="Repair booked").ok
    assert _status(w.run) == ChecklistRunStatus.DONE
    assert AuditEvent.unscoped.filter(action="run.resolve_flag", target_id=w.run.id).exists()


def test_resolving_a_flag_with_items_left_sends_the_run_back_to_in_progress(w):
    runs.flag_run(w.run, w.staff, reason="No supplies")
    assert runs.resolve_run_flag(w.run, w.supervisor).ok
    assert _status(w.run) == ChecklistRunStatus.IN_PROGRESS


def test_staff_cannot_resolve_or_cancel(w):
    runs.flag_run(w.run, w.staff, reason="No supplies")
    assert runs.resolve_run_flag(w.run, w.staff).code == "forbidden"
    assert runs.cancel_run(w.run, w.staff).code == "forbidden"


def test_a_supervisor_cancels_a_run(w):
    assert runs.cancel_run(w.run, w.supervisor).ok
    assert _status(w.run) == ChecklistRunStatus.CANCELLED


def test_another_organisation_gets_not_found(w):
    outsider = MembershipFactory(organisation=OrganisationFactory(), role="owner")
    assert runs.cancel_run(w.run, outsider).code == "not_found"


def test_run_actions_drive_the_buttons(w):
    assert runs.run_actions(w.run, w.staff) == ["tick", "skip", "flag"]
    assert "cancel" in runs.run_actions(w.run, w.supervisor)
    assert runs.run_actions(w.run, w.other_staff) == []


# --- visibility ---


def _visible(membership):
    with tenant_context(membership.organisation):
        return set(ChecklistRun.objects.visible_to(membership).values_list("id", flat=True))


def test_staff_see_only_runs_for_shifts_and_locations_they_are_rostered_on(w):
    elsewhere = ChecklistRunFactory(organisation=w.org)  # a different location, no roster
    assert w.run.id in _visible(w.staff)
    assert elsewhere.id not in _visible(w.staff)
    assert w.run.id not in _visible(w.other_staff)


def test_supervisors_see_their_locations(w):
    other_location = LocationFactory(organisation=w.org)
    MembershipLocationFactory(organisation=w.org, membership=w.supervisor, location=other_location)
    assert w.run.id not in _visible(w.supervisor)
    assert w.run.id in _visible(MembershipFactory(organisation=w.org, role="owner"))


def test_overdue_is_derived_for_runs(w):
    ChecklistRun.unscoped.filter(pk=w.run.pk).update(due_at=timezone.now() - timedelta(minutes=1))
    w.run.refresh_from_db()
    assert w.run.is_overdue is True
    with tenant_context(w.org):
        annotated = ChecklistRun.objects.annotate_overdue().get(pk=w.run.pk)
    assert annotated.is_overdue is True
