"""Creates the "Demo Guest House" organisation with sample data. Safe to run
more than once (every write is an idempotent get_or_create).
"""

from __future__ import annotations

from datetime import time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from checklists.generator import generate_runs
from checklists.models import ChecklistTemplate, RecurrenceKind, RecurrenceRule
from organisations.models import (
    Location,
    Membership,
    MembershipRole,
    Organisation,
    Shift,
    ShiftAssignment,
)
from organisations.tenancy import tenant_context
from tasks.models import Task

# key, phone, pin, name, role
DEMO_USERS = [
    ("owner", "+256700000001", "1111", "Olivia Owner", MembershipRole.OWNER),
    ("manager", "+256700000002", "2222", "Martin Manager", MembershipRole.MANAGER),
    ("supervisor", "+256700000003", "3333", "Susan Supervisor", MembershipRole.SUPERVISOR),
    ("staff1", "+256700000004", "4444", "Peter Okello", MembershipRole.STAFF),
    ("staff2", "+256700000005", "5555", "Grace Nakato", MembershipRole.STAFF),
]

CHECKLIST_ITEMS = [
    "Unlock stores",
    "Check fridge temperatures",
    "Count float (UGX)",
    "Wipe counters",
]


class Command(BaseCommand):
    help = 'Creates the "Demo Guest House" demo organisation. Safe to run twice.'

    def handle(self, *args: object, **options: object) -> None:
        org, _ = Organisation.objects.get_or_create(
            slug="demo", defaults={"name": "Demo Guest House"}
        )

        with tenant_context(org):
            main_building = self._location("Main building", 0)
            self._location("Restaurant", 1)

            weekdays = list(range(5))  # Mon-Fri
            morning, _ = Shift.objects.get_or_create(
                organisation=org,
                location=main_building,
                name="Morning",
                defaults={"start_time": time(6, 0), "end_time": time(14, 0), "weekdays": weekdays},
            )
            evening, _ = Shift.objects.get_or_create(
                organisation=org,
                location=main_building,
                name="Evening",
                defaults={"start_time": time(14, 0), "end_time": time(22, 0), "weekdays": weekdays},
            )

            memberships = {}
            for key, phone, pin, name, role in DEMO_USERS:
                user, created = User.objects.get_or_create(
                    phone_e164=phone, defaults={"name": name, "must_set_pin": False}
                )
                if created:
                    user.set_password(pin)
                    user.save(update_fields=["password"])
                membership, _ = Membership.objects.get_or_create(
                    organisation=org, user=user, defaults={"role": role}
                )
                memberships[key] = membership

            self._roster_this_week(morning, memberships["staff1"])
            self._roster_this_week(evening, memberships["staff2"])

            self._sample_tasks(main_building, evening, memberships)
            self._opening_checklist(main_building, morning, memberships["manager"])

        # What Beat would do on its next pass (idempotent).
        generate_runs(org)
        self._print_logins()

    def _location(self, name: str, sort_order: int) -> Location:
        location, _ = Location.objects.get_or_create(name=name, defaults={"sort_order": sort_order})
        return location

    def _roster_this_week(self, shift: Shift, membership: Membership) -> None:
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday())
        for offset in range(5):  # Mon-Fri
            ShiftAssignment.objects.get_or_create(
                shift=shift, membership=membership, date=monday + timedelta(days=offset)
            )

    def _sample_tasks(self, location: Location, shift: Shift, memberships: dict) -> None:
        now = timezone.now()
        Task.objects.get_or_create(
            title="Clean Room 12",
            location=location,
            defaults={
                "assignee_membership": memberships["staff1"],
                "due_at": now + timedelta(hours=2),
                "created_by": memberships["manager"],
            },
        )
        Task.objects.get_or_create(
            title="Restock towels",
            location=location,
            defaults={
                "assignee_shift": shift,
                "shift_date": timezone.localdate(),
                "due_at": now + timedelta(hours=4),
                "created_by": memberships["manager"],
            },
        )
        Task.objects.get_or_create(
            title="Check pool chemicals",
            location=location,
            defaults={
                "assignee_location": True,
                "due_at": now + timedelta(hours=6),
                "created_by": memberships["manager"],
            },
        )

    def _opening_checklist(self, location: Location, shift: Shift, created_by: Membership) -> None:
        template, _ = ChecklistTemplate.objects.get_or_create(
            location=location,
            name="Opening checklist",
            defaults={"shift": shift, "created_by": created_by},
        )
        for order, label in enumerate(CHECKLIST_ITEMS, start=1):
            template.items.get_or_create(order=order, defaults={"label": label})

        RecurrenceRule.objects.get_or_create(
            template=template,
            kind=RecurrenceKind.DAILY,
            defaults={"times": [time(7, 0)], "starts_on": timezone.localdate()},
        )

    def _print_logins(self) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Demo Guest House — demo logins"))
        self.stdout.write(f"{'Role':<12}{'Phone':<18}{'PIN':<6}{'Name'}")
        for _key, phone, pin, name, role in DEMO_USERS:
            self.stdout.write(f"{role:<12}{phone:<18}{pin:<6}{name}")
