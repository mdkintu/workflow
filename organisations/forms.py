"""Location and shift forms (docs/04-design.md §4.1 /org/locations/,
/org/shifts/). Querysets are set per request from the tenant-scoped managers,
so another organisation's location is never a valid choice."""

from __future__ import annotations

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from organisations.models import Location, Shift

WEEKDAY_CHOICES = [
    (0, _("Mon")),
    (1, _("Tue")),
    (2, _("Wed")),
    (3, _("Thu")),
    (4, _("Fri")),
    (5, _("Sat")),
    (6, _("Sun")),
]


class LocationForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=80)

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if Location.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError(_("There's already a location with that name."))
        return name


class ShiftForm(forms.Form):
    location = forms.ModelChoiceField(label=_("Location"), queryset=None)
    name = forms.CharField(label=_("Name"), max_length=60)
    start_time = forms.TimeField(label=_("Starts"))
    end_time = forms.TimeField(label=_("Ends"), help_text=_("Earlier than the start = overnight."))
    weekdays = forms.TypedMultipleChoiceField(
        label=_("Days"),
        choices=WEEKDAY_CHOICES,
        coerce=int,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = Location.objects.filter(is_active=True)

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        location, name = data.get("location"), data.get("name", "").strip()
        if location and name:
            if Shift.objects.filter(location=location, name__iexact=name).exists():
                self.add_error("name", _("That location already has a shift with this name."))
            data["name"] = name
        if data.get("weekdays"):
            data["weekdays"] = sorted(data["weekdays"])
        return data
