"""checklists.generator: materialising recurring checklist runs ahead of time
(ADR-08, docs/02-architecture.md §5, docs/01-requirements.md F2.2).

The organisation timezone is Africa/Kampala (UTC+3, no DST), so 07:00 local
is 04:00 UTC. Every test passes an explicit `now` so it's deterministic.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from checklists.generator import generate_runs
from checklists.models import (
    ChecklistItem,
    ChecklistRun,
    ChecklistRunItem,
    ChecklistRunStatus,
    RecurrenceKind,
)
from checklists.services import set_rule_active, update_item
from checklists.tasks import generate_checklist_runs
from tests.factories import (
    ChecklistItemFactory,
    ChecklistRunItemTickFactory,
    ChecklistTemplateFactory,
    LocationFactory,
    OrganisationFactory,
    RecurrenceRuleFactory,
    ShiftFactory,
)

pytestmark = pytest.mark.django_db

# Monday 13 October 2031, 06:00 in Kampala.
NOW = datetime(2031, 10, 13, 3, 0, tzinfo=UTC)


def _utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


@pytest.fixture
def template(org_a):
    location = LocationFactory(organisation=org_a, name="Bar")
    template = ChecklistTemplateFactory(organisation=org_a, location=location, name="Opening")
    for order, label in [(1, "Unlock stores"), (2, "Fridges ≤ 5°C"), (3, "Count float")]:
        ChecklistItemFactory(organisation=org_a, template=template, order=order, label=label)
    return template


def _runs(org):
    return ChecklistRun.unscoped.filter(organisation=org).order_by("occurrence_start")


def test_a_daily_rule_creates_one_run_per_day_inside_the_48_hour_horizon(org_a, template):
    RecurrenceRuleFactory(
        organisation=org_a,
        template=template,
        kind=RecurrenceKind.DAILY,
        times=[time(7, 0)],
        starts_on=date(2031, 10, 1),
        due_offset_min=45,
    )
    created = generate_runs(org_a, now=NOW)

    runs = list(_runs(org_a))
    assert created == 2
    assert [r.occurrence_start for r in runs] == [_utc(2031, 10, 13, 4), _utc(2031, 10, 14, 4)]
    assert runs[0].due_at == _utc(2031, 10, 13, 4, 45)
    assert runs[0].name == "Opening"
    assert runs[0].location == template.location
    assert runs[0].status == ChecklistRunStatus.PENDING
    assert runs[0].shift is None


def test_items_are_copied_in_order_and_inactive_items_are_left_out(org_a, template):
    ChecklistItemFactory(
        organisation=org_a, template=template, order=4, label="Retired", is_active=False
    )
    RecurrenceRuleFactory(
        organisation=org_a, template=template, times=[time(7, 0)], starts_on=date(2031, 10, 1)
    )
    generate_runs(org_a, now=NOW)

    run = _runs(org_a).first()
    labels = list(ChecklistRunItem.unscoped.filter(run=run).values_list("label", flat=True))
    assert labels == ["Unlock stores", "Fridges ≤ 5°C", "Count float"]


def test_generation_is_idempotent(org_a, template):
    RecurrenceRuleFactory(
        organisation=org_a, template=template, times=[time(7, 0)], starts_on=date(2031, 10, 1)
    )
    generate_runs(org_a, now=NOW)
    assert generate_runs(org_a, now=NOW) == 0
    assert generate_runs(org_a, now=NOW + timedelta(minutes=15)) == 0
    assert _runs(org_a).count() == 2
    assert ChecklistRunItem.unscoped.filter(organisation=org_a).count() == 6


def test_an_occurrence_already_past_its_due_time_is_not_created(org_a, template):
    RecurrenceRuleFactory(
        organisation=org_a,
        template=template,
        times=[time(5, 0)],
        starts_on=date(2031, 10, 1),
        due_offset_min=30,
    )
    generate_runs(org_a, now=NOW)  # 05:00 + 30 min is before 06:00 today
    assert [r.occurrence_start for r in _runs(org_a)] == [
        _utc(2031, 10, 14, 2),
        _utc(2031, 10, 15, 2),
    ]


def test_an_occurrence_that_has_started_but_isnt_due_yet_is_created(org_a, template):
    RecurrenceRuleFactory(
        organisation=org_a,
        template=template,
        times=[time(5, 30)],
        starts_on=date(2031, 10, 1),
        due_offset_min=60,
    )
    generate_runs(org_a, now=NOW)  # 05:30 local, due 06:30 — still open at 06:00
    assert _runs(org_a).first().occurrence_start == _utc(2031, 10, 13, 2, 30)


def test_a_weekly_rule_only_runs_on_its_days(org_a, template):
    RecurrenceRuleFactory(
        organisation=org_a,
        template=template,
        kind=RecurrenceKind.WEEKLY,
        times=[time(9, 0)],
        weekdays=[1],
        starts_on=date(2031, 10, 1),  # Tuesdays
    )
    generate_runs(org_a, now=NOW)
    assert [r.occurrence_start for r in _runs(org_a)] == [_utc(2031, 10, 14, 6)]


def test_several_times_a_day(org_a, template):
    RecurrenceRuleFactory(
        organisation=org_a,
        template=template,
        times=[time(10, 0), time(16, 0)],
        starts_on=date(2031, 10, 1),
    )
    generate_runs(org_a, now=NOW)
    assert _runs(org_a).count() == 4


def test_a_shift_start_rule_follows_the_shift_and_assigns_the_run_to_it(org_a, template):
    evening = ShiftFactory(
        organisation=org_a,
        location=template.location,
        name="Evening",
        start_time=time(14, 0),
        end_time=time(22, 0),
        weekdays=[0, 2],  # Mon, Wed
    )
    template.shift = evening
    template.save()
    RecurrenceRuleFactory(
        organisation=org_a,
        template=template,
        kind=RecurrenceKind.SHIFT_START,
        times=[],
        starts_on=date(2031, 10, 1),
    )
    generate_runs(org_a, now=NOW)

    runs = list(_runs(org_a))
    assert [r.occurrence_start for r in runs] == [_utc(2031, 10, 13, 11)]  # Mon only in 48h
    assert runs[0].shift == evening
    assert runs[0].shift_date == date(2031, 10, 13)


def test_a_daily_rule_on_a_shift_template_goes_to_that_shift(org_a, template):
    morning = ShiftFactory(organisation=org_a, location=template.location, name="Morning")
    template.shift = morning
    template.save()
    RecurrenceRuleFactory(
        organisation=org_a, template=template, times=[time(7, 0)], starts_on=date(2031, 10, 1)
    )
    generate_runs(org_a, now=NOW)
    run = _runs(org_a).first()
    assert run.shift == morning
    assert run.shift_date == date(2031, 10, 13)


def test_start_and_end_dates_are_respected(org_a, template):
    RecurrenceRuleFactory(
        organisation=org_a,
        template=template,
        times=[time(7, 0)],
        starts_on=date(2031, 10, 14),
        ends_on=date(2031, 10, 14),
    )
    generate_runs(org_a, now=NOW)
    assert [r.occurrence_start for r in _runs(org_a)] == [_utc(2031, 10, 14, 4)]


def test_inactive_rules_templates_and_empty_templates_generate_nothing(org_a, template):
    RecurrenceRuleFactory(
        organisation=org_a,
        template=template,
        times=[time(7, 0)],
        starts_on=date(2031, 10, 1),
        is_active=False,
    )
    empty = ChecklistTemplateFactory(organisation=org_a, location=template.location)
    RecurrenceRuleFactory(
        organisation=org_a, template=empty, times=[time(7, 0)], starts_on=date(2031, 10, 1)
    )
    retired = ChecklistTemplateFactory(
        organisation=org_a, location=template.location, is_active=False
    )
    ChecklistItemFactory(organisation=org_a, template=retired)
    RecurrenceRuleFactory(
        organisation=org_a, template=retired, times=[time(7, 0)], starts_on=date(2031, 10, 1)
    )
    assert generate_runs(org_a, now=NOW) == 0


def test_only_the_given_organisation_is_generated(org_a, org_b, template):
    RecurrenceRuleFactory(
        organisation=org_a, template=template, times=[time(7, 0)], starts_on=date(2031, 10, 1)
    )
    other = ChecklistTemplateFactory(organisation=org_b)
    ChecklistItemFactory(organisation=org_b, template=other)
    RecurrenceRuleFactory(
        organisation=org_b, template=other, times=[time(7, 0)], starts_on=date(2031, 10, 1)
    )
    generate_runs(org_a, now=NOW)
    assert _runs(org_b).count() == 0


def test_the_beat_task_fans_out_to_every_active_organisation(org_a, template):
    """CELERY_TASK_ALWAYS_EAGER in dev/test settings runs the fan-out inline."""
    RecurrenceRuleFactory(
        organisation=org_a, template=template, times=[time(7, 0)], starts_on=date(2020, 1, 1)
    )
    inactive_org = OrganisationFactory(is_active=False)
    tpl = ChecklistTemplateFactory(organisation=inactive_org)
    ChecklistItemFactory(organisation=inactive_org, template=tpl)
    RecurrenceRuleFactory(
        organisation=inactive_org, template=tpl, times=[time(7, 0)], starts_on=date(2020, 1, 1)
    )

    generate_checklist_runs()

    assert _runs(org_a).exists()
    assert not _runs(inactive_org).exists()


def test_pausing_a_rule_cancels_its_future_untouched_runs_only(org_a, template, manager_membership):
    rule = RecurrenceRuleFactory(
        organisation=org_a, template=template, times=[time(7, 0)], starts_on=date(2031, 10, 1)
    )
    generate_runs(org_a, now=NOW)
    started, future = list(_runs(org_a))
    ChecklistRunItemTickFactory(
        organisation=org_a,
        run_item=ChecklistRunItem.unscoped.filter(run=started).first(),
        membership=manager_membership,
    )
    ChecklistRun.unscoped.filter(pk=started.pk).update(status=ChecklistRunStatus.IN_PROGRESS)

    set_rule_active(actor=manager_membership, rule=rule, active=False, now=NOW)

    started.refresh_from_db()
    future.refresh_from_db()
    assert started.status == ChecklistRunStatus.IN_PROGRESS
    assert future.status == ChecklistRunStatus.CANCELLED
    rule.refresh_from_db()
    assert rule.is_active is False


def test_editing_a_template_item_does_not_change_runs_already_generated(
    org_a, template, manager_membership
):
    RecurrenceRuleFactory(
        organisation=org_a, template=template, times=[time(7, 0)], starts_on=date(2031, 10, 1)
    )
    generate_runs(org_a, now=NOW)

    stored = ChecklistItem.unscoped.get(template=template, order=1)
    update_item(actor=manager_membership, item=stored, label="Unlock ALL stores")

    labels = set(ChecklistRunItem.unscoped.filter(order=1).values_list("label", flat=True))
    assert labels == {"Unlock stores"}
