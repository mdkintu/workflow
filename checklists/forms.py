"""Checklist template, item and schedule forms (docs/01-requirements.md
F2.1-F2.2). Querysets are set per request in __init__ — never at class
level, where a tenant-scoped manager would raise at import time (CLAUDE.md).
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from checklists.models import ChecklistTemplate, RecurrenceKind
from organisations.forms import WEEKDAY_CHOICES
from organisations.models import Location, Shift


class TemplateForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=120)
    location = forms.ModelChoiceField(label=_("Location"), queryset=None)
    shift = forms.ModelChoiceField(
        label=_("Shift"),
        queryset=None,
        required=False,
        help_text=_("Leave empty for anyone working at the location."),
    )
    is_active = forms.BooleanField(label=_("Active"), required=False, initial=True)

    def __init__(self, *args: Any, instance: ChecklistTemplate | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.instance = instance
        self.fields["location"].queryset = Location.objects.filter(is_active=True)
        self.fields["shift"].queryset = Shift.objects.filter(is_active=True).select_related(
            "location"
        )

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        location, shift, name = data.get("location"), data.get("shift"), data.get("name", "")
        if location and shift and shift.location_id != location.id:
            self.add_error("shift", _("That shift is at a different location."))
        if location and name:
            clash = ChecklistTemplate.objects.filter(location=location, name__iexact=name.strip())
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                self.add_error("name", _("This location already has a checklist with that name."))
        return data


class ItemForm(forms.Form):
    label = forms.CharField(label=_("Item"), max_length=160)
    photo_required = forms.BooleanField(label=_("Photo required"), required=False)
    # Unticked = skippable, the safe default for a checkbox that's left alone.
    must_do = forms.BooleanField(label=_("Must be done (can't skip)"), required=False)


class RuleForm(forms.Form):
    kind = forms.ChoiceField(label=_("Repeats"), choices=RecurrenceKind.choices)
    times = forms.CharField(
        label=_("Times"),
        required=False,
        help_text=_("24-hour, comma-separated, e.g. 07:00, 13:00"),
    )
    weekdays = forms.TypedMultipleChoiceField(
        label=_("Days"), choices=WEEKDAY_CHOICES, coerce=int, required=False
    )
    due_offset_min = forms.IntegerField(
        label=_("Due after (minutes)"), min_value=5, max_value=24 * 60, initial=60
    )
    available_before_min = forms.IntegerField(
        label=_("Open early by (minutes)"), min_value=0, max_value=12 * 60, initial=0
    )
    starts_on = forms.DateField(label=_("From"))
    ends_on = forms.DateField(label=_("Until"), required=False)

    def __init__(self, *args: Any, template: ChecklistTemplate, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.template = template

    def clean_times(self) -> list[time]:
        raw = self.cleaned_data.get("times", "")
        parsed = []
        for part in filter(None, (p.strip() for p in raw.split(","))):
            try:
                parsed.append(datetime.strptime(part, "%H:%M").time())
            except ValueError as exc:
                raise forms.ValidationError(
                    _("%(value)s isn't a time like 07:00.") % {"value": part}
                ) from exc
        return sorted(set(parsed))

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        kind = data.get("kind")
        if kind == RecurrenceKind.SHIFT_START:
            if self.template.shift_id is None:
                self.add_error(
                    "kind", _("“At shift start” needs the checklist to be linked to a shift.")
                )
            data["times"], data["weekdays"] = [], []
        elif not data.get("times"):
            self.add_error("times", _("Add at least one time."))
        if kind == RecurrenceKind.WEEKLY and not data.get("weekdays"):
            self.add_error("weekdays", _("Choose at least one day."))
        if kind == RecurrenceKind.DAILY:
            data["weekdays"] = []
        starts, ends = data.get("starts_on"), data.get("ends_on")
        if starts and ends and ends < starts:
            self.add_error("ends_on", _("The end date is before the start date."))
        return data
