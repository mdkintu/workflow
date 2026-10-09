"""The task create/edit form (docs/04-design.md §3 S8).

Querysets are set per request in __init__, from the tenant-scoped managers
(never at class level: that runs at import time, with no organisation yet):
a location, shift or person from another organisation simply isn't a valid
choice (CLAUDE.md: "foreign keys between tenant records point to the same
organisation"). Due date/time are entered in the organisation's timezone,
which TenantMiddleware activates for the request.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from organisations.models import Location, Membership, MembershipLocation, Shift
from organisations.permissions import Role, has_role
from tasks.models import Task

ASSIGN_PERSON, ASSIGN_SHIFT, ASSIGN_LOCATION = "person", "shift", "location"
REMINDER_CHOICES = [
    ("", _("Organisation default")),
    ("0", _("No reminder")),
    ("15", _("15 minutes before")),
    ("30", _("30 minutes before")),
    ("60", _("1 hour before")),
    ("120", _("2 hours before")),
]
FLAG_REASONS = [_("No supplies"), _("Room occupied"), _("Broken equipment"), _("Other")]
# docs/04-design.md §5.2 T1: a due time may be up to an hour in the past, so
# work that was just done can be back-filled.
BACKFILL_WINDOW = timedelta(hours=1)


def allowed_locations(membership: Membership):
    """Supervisors and Managers with linked locations may only create tasks
    there; without linked locations (and for Owners) every active location."""
    locations = Location.objects.filter(is_active=True)
    if has_role(membership, Role.OWNER):
        return locations
    linked = MembershipLocation.objects.filter(membership=membership).values("location_id")
    if linked.exists():
        return locations.filter(id__in=linked)
    return locations


class TaskForm(forms.Form):
    title = forms.CharField(label=_("Title"), max_length=120)
    description = forms.CharField(
        label=_("Details"), max_length=1000, required=False, widget=forms.Textarea
    )
    location = forms.ModelChoiceField(label=_("Location"), queryset=None)
    assign_type = forms.ChoiceField(
        label=_("Assign to"),
        choices=[
            (ASSIGN_PERSON, _("Person")),
            (ASSIGN_SHIFT, _("Shift")),
            (ASSIGN_LOCATION, _("Anyone at this location")),
        ],
        widget=forms.RadioSelect,
    )
    assignee_membership = forms.ModelChoiceField(label=_("Person"), queryset=None, required=False)
    assignee_shift = forms.ModelChoiceField(label=_("Shift"), queryset=None, required=False)
    shift_date = forms.DateField(label=_("Shift date"), required=False)
    due_date = forms.DateField(label=_("Due date"))
    due_time = forms.TimeField(label=_("Due time"))
    photo_required = forms.BooleanField(label=_("Photo required"), required=False)
    reminder_lead_min = forms.TypedChoiceField(
        label=_("Reminder"),
        choices=REMINDER_CHOICES,
        coerce=int,
        empty_value=None,
        required=False,
    )

    def __init__(self, *args: Any, membership: Membership, instance: Task | None = None, **kw):
        if instance is not None and "initial" not in kw:
            kw["initial"] = self.initial_from(instance)
        super().__init__(*args, **kw)
        self.instance = instance
        self.fields["location"].queryset = allowed_locations(membership)
        self.fields["assignee_membership"].queryset = (
            Membership.objects.filter(is_active=True).select_related("user").order_by("user__name")
        )
        self.fields["assignee_shift"].queryset = Shift.objects.filter(
            is_active=True
        ).select_related("location")

    @staticmethod
    def initial_from(task: Task) -> dict[str, Any]:
        local_due = timezone.localtime(task.due_at)
        if task.assignee_membership_id:
            assign_type = ASSIGN_PERSON
        elif task.assignee_shift_id:
            assign_type = ASSIGN_SHIFT
        else:
            assign_type = ASSIGN_LOCATION
        return {
            "title": task.title,
            "description": task.description,
            "location": task.location_id,
            "assign_type": assign_type,
            "assignee_membership": task.assignee_membership_id,
            "assignee_shift": task.assignee_shift_id,
            "shift_date": task.shift_date,
            "due_date": local_due.date(),
            "due_time": local_due.time().replace(second=0, microsecond=0),
            "photo_required": task.photo_required,
            "reminder_lead_min": task.reminder_lead_min,
        }

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        location = data.get("location")
        assign_type = data.get("assign_type")

        if data.get("due_date") and data.get("due_time"):
            due_at = datetime.combine(
                data["due_date"], data["due_time"], tzinfo=timezone.get_current_timezone()
            )
            unchanged = self.instance is not None and self.instance.due_at == due_at
            if not unchanged and due_at < timezone.now() - BACKFILL_WINDOW:
                self.add_error("due_date", _("The due time is in the past."))
            data["due_at"] = due_at

        person, shift = data.get("assignee_membership"), data.get("assignee_shift")
        if assign_type == ASSIGN_PERSON and person is None:
            self.add_error("assignee_membership", _("Choose who does this."))
        if assign_type == ASSIGN_SHIFT:
            if shift is None:
                self.add_error("assignee_shift", _("Choose a shift."))
            elif location is not None and shift.location_id != location.id:
                self.add_error("assignee_shift", _("That shift is at a different location."))
            if data.get("shift_date") is None:
                self.add_error("shift_date", _("Choose the day of the shift."))

        # Exactly one assignee (the database enforces this too).
        data["assignee_membership"] = person if assign_type == ASSIGN_PERSON else None
        data["assignee_shift"] = shift if assign_type == ASSIGN_SHIFT else None
        data["shift_date"] = data.get("shift_date") if assign_type == ASSIGN_SHIFT else None
        data["assignee_location"] = assign_type == ASSIGN_LOCATION
        return data

    def task_fields(self) -> dict[str, Any]:
        data = self.cleaned_data
        return {
            "title": data["title"],
            "description": data["description"],
            "location": data["location"],
            "assignee_membership": data["assignee_membership"],
            "assignee_shift": data["assignee_shift"],
            "assignee_location": data["assignee_location"],
            "shift_date": data["shift_date"],
            "due_at": data["due_at"],
            "photo_required": data["photo_required"],
            "reminder_lead_min": data["reminder_lead_min"],
        }


class SelfTaskForm(forms.Form):
    """One-off self-task: staff creates a task for themselves."""

    title = forms.CharField(label=_("Title"), max_length=120)
    description = forms.CharField(
        label=_("Details"), max_length=1000, required=False, widget=forms.Textarea
    )
    due_date = forms.DateField(label=_("Due date"))
    due_time = forms.TimeField(label=_("Due time"))
    photo_required = forms.BooleanField(label=_("Photo required"), required=False)
    reminder_lead_min = forms.TypedChoiceField(
        label=_("Reminder"),
        choices=REMINDER_CHOICES,
        coerce=int,
        empty_value=None,
        required=False,
    )

    def __init__(self, *args: Any, membership: Membership, **kw):
        super().__init__(*args, **kw)
        self.membership = membership

    def clean(self) -> dict[str, Any]:
        data = super().clean()

        if data.get("due_date") and data.get("due_time"):
            due_at = datetime.combine(
                data["due_date"], data["due_time"], tzinfo=timezone.get_current_timezone()
            )
            if due_at < timezone.now() - BACKFILL_WINDOW:
                self.add_error("due_date", _("The due time is in the past."))
            data["due_at"] = due_at

        return data

    def task_fields(self) -> dict[str, Any]:
        """Return Task field values for a self-task."""
        data = self.cleaned_data
        return {
            "title": data["title"],
            "description": data["description"],
            "location": self.membership.organisation.locations.first(),
            "assignee_membership": self.membership,
            "assignee_shift": None,
            "assignee_location": False,
            "shift_date": None,
            "due_at": data["due_at"],
            "photo_required": data["photo_required"],
            "reminder_lead_min": data["reminder_lead_min"],
        }


class SelfTaskRecurringForm(SelfTaskForm):
    """Recurring self-task with frequency and time settings."""

    DAILY = "daily"
    WEEKLY = "weekly"

    RECURRENCE_CHOICES = [(DAILY, _("Daily")), (WEEKLY, _("Weekly"))]

    recurs = forms.BooleanField(label=_("Repeating task"), required=False)
    kind = forms.ChoiceField(
        label=_("Frequency"), choices=RECURRENCE_CHOICES, required=False, initial=DAILY
    )
    weekdays = forms.MultipleChoiceField(
        label=_("Days"),
        choices=[
            (0, _("Monday")),
            (1, _("Tuesday")),
            (2, _("Wednesday")),
            (3, _("Thursday")),
            (4, _("Friday")),
            (5, _("Saturday")),
            (6, _("Sunday")),
        ],
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    ends_on = forms.DateField(label=_("Stops on"), required=False)

    def clean(self) -> dict[str, Any]:
        data = super().clean()

        if data.get("recurs"):
            if not data.get("kind"):
                self.add_error("kind", _("Choose frequency"))
            if data.get("kind") == self.WEEKLY and not data.get("weekdays"):
                self.add_error("weekdays", _("Choose at least one day"))

        return data

    def rule_fields(self) -> dict[str, Any]:
        """Return TaskRecurrenceRule field values."""
        from tasks.models import TaskRecurrenceRule

        data = self.cleaned_data
        if not data.get("recurs"):
            return {}

        return {
            "kind": data["kind"],
            "weekdays": [int(d) for d in data.get("weekdays", [])],
            "starts_on": data["due_date"],
            "ends_on": data.get("ends_on"),
            "times": [[data["due_time"].hour, data["due_time"].minute]],
        }
