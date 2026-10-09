"""organisations.management.commands.seed_demo: must be idempotent."""

import pytest
from django.core.management import call_command

from checklists.models import ChecklistItem, ChecklistTemplate, RecurrenceRule
from organisations.models import Location, Membership, Organisation, Shift, ShiftAssignment
from organisations.tenancy import tenant_context
from tasks.models import Task

pytestmark = pytest.mark.django_db


def _counts(org: Organisation) -> dict:
    with tenant_context(org):
        return {
            "locations": Location.objects.count(),
            "memberships": Membership.objects.count(),
            "shifts": Shift.objects.count(),
            "shift_assignments": ShiftAssignment.objects.count(),
            "tasks": Task.objects.count(),
            "templates": ChecklistTemplate.objects.count(),
            "items": ChecklistItem.objects.count(),
            "rules": RecurrenceRule.objects.count(),
        }


def test_seed_demo_is_idempotent():
    call_command("seed_demo")
    org = Organisation.objects.get(slug="demo")
    first = _counts(org)

    call_command("seed_demo")
    second = _counts(org)

    assert first == second
    assert first == {
        "locations": 2,
        "memberships": 5,
        "shifts": 2,
        "shift_assignments": 10,
        "tasks": 3,
        "templates": 1,
        "items": 4,
        "rules": 1,
    }
